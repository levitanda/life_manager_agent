/**
 * Local Baileys WhatsApp bridge.
 *
 * Listens on 127.0.0.1:BRIDGE_PORT, exposes a tiny HTTP API consumed by the
 * Python life-agent. Authentication is one-time via QR — auth state is then
 * persisted under AUTH_DIR so we survive restarts.
 *
 * Endpoints:
 *   GET  /status      → { ready, has_qr, error }
 *   GET  /qr          → { qr }  (raw QR string; render with `qrcode-terminal` or any QR lib)
 *   GET  /groups      → { groups: [{ id, name, size }] }
 *   POST /send        → body { chatId, text }
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

let sock = null;
let currentQR = null;
let isReady = false;
let lastConnError = null;
let reconnectAttempt = 0;

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
      console.log('\n=== WhatsApp QR — scan with your phone (WhatsApp → Linked devices → Link a device) ===\n');
      qrcode.generate(qr, { small: true });
      console.log('\nQR also available at GET /qr');
    }

    if (connection === 'open') {
      isReady = true;
      currentQR = null;
      lastConnError = null;
      reconnectAttempt = 0;
      console.log('WhatsApp connected — bridge is ready.');
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
}

const app = express();
app.use(express.json({ limit: '256kb' }));

app.get('/status', (_req, res) => {
  res.json({
    ready: isReady,
    has_qr: !!currentQR,
    error: lastConnError,
  });
});

app.get('/qr', (_req, res) => {
  if (!currentQR) {
    return res
      .status(404)
      .json({ error: isReady ? 'already_authenticated' : 'no_qr_yet' });
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

app.post('/send', async (req, res) => {
  if (!isReady) return res.status(503).json({ error: 'not_ready' });
  const { chatId, text } = req.body || {};
  if (!chatId || !text) {
    return res.status(400).json({ error: 'chatId and text required' });
  }
  try {
    await sock.sendMessage(chatId, { text });
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
