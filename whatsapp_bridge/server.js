/**
 * Local Baileys WhatsApp bridge.
 *
 * Listens on 127.0.0.1:BRIDGE_PORT. Maintains in-memory stores of chats and
 * recent messages, populated from Baileys events. Stores are lost on restart
 * (rebuild from incoming traffic + initial sync).
 *
 * Endpoints:
 *   GET  /status                  → { ready, has_qr, error }
 *   GET  /qr                      → { qr }
 *   GET  /groups                  → { groups: [...] }  (groups participation snapshot)
 *   GET  /chats                   → { chats: [...] }   (all known chats: groups + 1-on-1)
 *   GET  /unread                  → { chats: [...] }   (unreadCount > 0, with recent messages)
 *   GET  /chat/:id/messages?limit → { messages: [...] }
 *   POST /find       { query }    → { matches: [...] }  (fuzzy search by chat name)
 *   POST /send       { chatId, text } → { ok: true }
 */

const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  Browsers,
} = require('@whiskeysockets/baileys');
const express = require('express');
const pino = require('pino');
const qrcode = require('qrcode-terminal');

const PORT = parseInt(process.env.BRIDGE_PORT || '3030', 10);
const AUTH_DIR = process.env.WA_AUTH_DIR || './auth_session';
const LOG_LEVEL = process.env.LOG_LEVEL || 'silent';
const MAX_MSGS_PER_CHAT = 50;

let sock = null;
let currentQR = null;
let isReady = false;
let lastConnError = null;
let reconnectAttempt = 0;

// In-memory state
const chatStore = new Map();        // chatId → { id, name, unreadCount, lastTs, isGroup }
const messageStore = new Map();     // chatId → [{ from, text, ts, fromMe, senderName }]

function upsertChat(partial) {
  if (!partial.id) return;
  const existing = chatStore.get(partial.id) || {};
  const merged = {
    id: partial.id,
    name: partial.name || partial.subject || existing.name || partial.id,
    unreadCount:
      partial.unreadCount !== undefined ? partial.unreadCount : existing.unreadCount || 0,
    lastTs: partial.lastTs || existing.lastTs || 0,
    isGroup: partial.id.endsWith('@g.us'),
  };
  chatStore.set(partial.id, merged);
}

function extractMessageText(msg) {
  const m = msg.message || {};
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage) return '[фото]' + (m.imageMessage.caption ? ': ' + m.imageMessage.caption : '');
  if (m.videoMessage) return '[видео]' + (m.videoMessage.caption ? ': ' + m.videoMessage.caption : '');
  if (m.audioMessage) return '[аудио]';
  if (m.documentMessage) return '[документ]' + (m.documentMessage.fileName ? ': ' + m.documentMessage.fileName : '');
  if (m.stickerMessage) return '[стикер]';
  if (m.contactMessage) return '[контакт]';
  if (m.locationMessage) return '[геолокация]';
  if (m.reactionMessage?.text) return `[реакция: ${m.reactionMessage.text}]`;
  return null;
}

function appendMessage(msg) {
  if (!msg.key?.remoteJid) return;
  const chatId = msg.key.remoteJid;
  const text = extractMessageText(msg);
  if (!text) return;

  const arr = messageStore.get(chatId) || [];
  arr.push({
    from: msg.key.participant || msg.key.remoteJid,
    text,
    ts: msg.messageTimestamp || Math.floor(Date.now() / 1000),
    fromMe: msg.key.fromMe || false,
    senderName: msg.pushName || null,
  });
  if (arr.length > MAX_MSGS_PER_CHAT) arr.shift();
  messageStore.set(chatId, arr);

  upsertChat({ id: chatId, lastTs: msg.messageTimestamp || Math.floor(Date.now() / 1000) });
}

async function startSocket() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: LOG_LEVEL }),
    printQRInTerminal: false,
    browser: Browsers.macOS('LifeAgent'),
    syncFullHistory: false,
    markOnlineOnConnect: false,
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      currentQR = qr;
      console.log('\n=== WhatsApp QR — scan with phone ===\n');
      qrcode.generate(qr, { small: true });
      console.log('\nQR also available at GET /qr');
    }

    if (connection === 'open') {
      isReady = true;
      currentQR = null;
      lastConnError = null;
      reconnectAttempt = 0;
      console.log('WhatsApp connected — bridge ready.');
      bootstrapGroups().catch((e) => console.error('group bootstrap failed', e));
    } else if (connection === 'close') {
      isReady = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      lastConnError = lastDisconnect?.error?.message || `code:${code}`;
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      console.log(`Connection closed (${code}). Reconnect: ${shouldReconnect}`);
      if (shouldReconnect) {
        reconnectAttempt++;
        const delay = Math.min(30000, 2000 * reconnectAttempt);
        setTimeout(startSocket, delay);
      } else {
        console.log('Logged out — delete auth_session/ and restart to re-pair.');
      }
    }
  });

  // Initial chat list from history sync
  sock.ev.on('messaging-history.set', ({ chats, messages }) => {
    for (const chat of chats || []) {
      upsertChat({
        id: chat.id,
        name: chat.name || chat.subject,
        unreadCount: chat.unreadCount || 0,
        lastTs: chat.conversationTimestamp,
      });
    }
    for (const m of messages || []) {
      appendMessage(m);
    }
  });

  sock.ev.on('chats.upsert', (chats) => {
    for (const c of chats) upsertChat({ id: c.id, name: c.name || c.subject, unreadCount: c.unreadCount });
  });

  sock.ev.on('chats.update', (updates) => {
    for (const u of updates) upsertChat({ id: u.id, unreadCount: u.unreadCount });
  });

  sock.ev.on('messages.upsert', ({ messages, type }) => {
    for (const m of messages) appendMessage(m);
    // Incoming messages bump unread; outgoing reset
    if (type === 'notify') {
      for (const m of messages) {
        if (!m.key.fromMe) {
          const existing = chatStore.get(m.key.remoteJid);
          if (existing) {
            existing.unreadCount = (existing.unreadCount || 0) + 1;
          }
        }
      }
    }
  });

  sock.ev.on('contacts.upsert', (contacts) => {
    for (const c of contacts) {
      // Personal contacts: prefer notify (display name) over verifiedName
      const name = c.notify || c.name || c.verifiedName;
      if (name && c.id) upsertChat({ id: c.id, name });
    }
  });
}

async function bootstrapGroups() {
  try {
    const groups = await sock.groupFetchAllParticipating();
    for (const g of Object.values(groups)) {
      upsertChat({ id: g.id, name: g.subject });
    }
  } catch (e) {
    console.error('groupFetchAllParticipating failed', e.message);
  }
}

// ─── HTTP API ──────────────────────────────────────────────────────────────

const app = express();
app.use(express.json({ limit: '256kb' }));

app.get('/status', (_req, res) => {
  res.json({ ready: isReady, has_qr: !!currentQR, error: lastConnError, chats_known: chatStore.size });
});

app.get('/qr', (_req, res) => {
  if (!currentQR) {
    return res.status(404).json({ error: isReady ? 'already_authenticated' : 'no_qr_yet' });
  }
  res.json({ qr: currentQR });
});

app.get('/groups', async (_req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  try {
    const chats = await sock.groupFetchAllParticipating();
    const groups = Object.values(chats).map((g) => ({
      id: g.id,
      name: g.subject,
      size: g.participants?.length || 0,
    }));
    res.json({ groups });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/chats', (_req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const list = Array.from(chatStore.values()).sort(
    (a, b) => (b.lastTs || 0) - (a.lastTs || 0),
  );
  res.json({ chats: list });
});

app.get('/unread', (_req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const list = Array.from(chatStore.values())
    .filter((c) => (c.unreadCount || 0) > 0)
    .sort((a, b) => (b.lastTs || 0) - (a.lastTs || 0))
    .map((c) => ({
      ...c,
      recentMessages: (messageStore.get(c.id) || []).slice(-15),
    }));
  res.json({ chats: list });
});

app.get('/recent', (req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const limit = parseInt(req.query.limit || '50', 10);
  const msgsPer = parseInt(req.query.messages_per_chat || '10', 10);

  const list = Array.from(chatStore.values())
    .filter((c) => messageStore.has(c.id))
    .sort((a, b) => (b.lastTs || 0) - (a.lastTs || 0))
    .slice(0, limit)
    .map((c) => {
      const messages = (messageStore.get(c.id) || []).slice(-msgsPer);
      const lastMsg = messages[messages.length - 1];
      return {
        ...c,
        recentMessages: messages,
        lastFromMe: lastMsg ? !!lastMsg.fromMe : null,
        lastMessageTs: lastMsg ? lastMsg.ts : null,
      };
    });
  res.json({ chats: list });
});

app.get('/chat/:id/messages', (req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const limit = parseInt(req.query.limit || '20', 10);
  const msgs = (messageStore.get(req.params.id) || []).slice(-limit);
  res.json({ messages: msgs });
});

app.post('/find', (req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const { query } = req.body || {};
  if (!query) return res.status(400).json({ error: 'query required' });
  const needle = String(query).toLowerCase();
  const matches = Array.from(chatStore.values())
    .filter((c) => (c.name || '').toLowerCase().includes(needle))
    .sort((a, b) => (b.lastTs || 0) - (a.lastTs || 0))
    .slice(0, 10);
  res.json({ matches });
});

app.post('/send', async (req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const { chatId, text } = req.body || {};
  if (!chatId || !text) {
    return res.status(400).json({ error: 'chatId and text required' });
  }
  try {
    // Verify the JID is on WhatsApp before sending — avoids hanging on invalid numbers
    if (chatId.endsWith('@s.whatsapp.net')) {
      const phone = chatId.split('@')[0];
      try {
        const [check] = await sock.onWhatsApp(phone);
        if (!check || !check.exists) {
          return res.status(404).json({ error: `Number ${phone} is not on WhatsApp` });
        }
      } catch (verifyErr) {
        // Continue; onWhatsApp can fail without meaning the number is invalid
      }
    }

    const sendPromise = sock.sendMessage(chatId, { text });
    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(() => reject(new Error('send_timeout_30s')), 30000),
    );
    await Promise.race([sendPromise, timeoutPromise]);

    const existing = chatStore.get(chatId);
    if (existing) existing.unreadCount = 0;
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`WA bridge listening on 127.0.0.1:${PORT}`);
});

startSocket().catch((err) => {
  console.error('Fatal: failed to start socket', err);
  process.exit(1);
});
