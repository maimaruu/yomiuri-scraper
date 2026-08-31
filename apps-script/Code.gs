const SPREADSHEET_ID = '10ka6pLQrFbtw0KNa_nmWIIjDQYLcwEqcGa_HIVnCBOY';
const LATEST_SHEET = 'latest';
const LOG_SHEET = 'tool_logs';

const LOG_HEADERS = [
  'event_id',
  'server_timestamp',
  'client_timestamp',
  'browser_id',
  'session_id',
  'event_type',
  'article_id',
  'rank',
  'title',
  'url',
  'message_index',
  'message_text',
  'presented_article_ids',
  'presented_titles',
  'metadata_json'
];

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('ニュース読解観察ツール')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function getLatestArticles() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = ss.getSheetByName(LATEST_SHEET);
  if (!sheet) {
    throw new Error('latest シートがまだありません。スクレイパーの実行完了を確認してください。');
  }

  const values = sheet.getDataRange().getValues();
  if (values.length < 2) return [];

  const headers = values[0].map(String);
  const idx = Object.fromEntries(headers.map((h, i) => [h, i]));

  return values.slice(1, 6)
    .filter(row => row.some(v => String(v).trim() !== ''))
    .map(row => ({
      rank: Number(row[idx.rank] || 0),
      article_id: String(row[idx.ID] || ''),
      collected_at: String(row[idx.collected_at] || ''),
      title: String(row[idx.title] || ''),
      source: String(row[idx.source] || '読売新聞'),
      published_at: String(row[idx.published_at] || ''),
      url: String(row[idx.url] || ''),
      category: String(row[idx.category] || ''),
      body: String(row[idx.body] || '')
    }));
}

function logEvent(payload) {
  payload = payload || {};
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  const sheet = getOrCreateLogSheet_(ss);

  const eventId = Utilities.getUuid();
  const now = new Date();

  const presented = Array.isArray(payload.presented_articles)
    ? payload.presented_articles
    : [];

  const row = [
    eventId,
    now,
    payload.client_timestamp || '',
    payload.browser_id || '',
    payload.session_id || '',
    payload.event_type || '',
    payload.article_id || '',
    payload.rank == null ? '' : payload.rank,
    payload.title || '',
    payload.url || '',
    payload.message_index == null ? '' : payload.message_index,
    payload.message_text || '',
    presented.map(a => a.article_id || '').join(' | '),
    presented.map(a => a.title || '').join(' | '),
    JSON.stringify(payload.metadata || {})
  ];

  ensureLogCapacity_(sheet, 1);
  sheet.appendRow(row);

  return {
    ok: true,
    event_id: eventId,
    server_timestamp: now.toISOString()
  };
}

function getOrCreateLogSheet_(ss) {
  let sheet = ss.getSheetByName(LOG_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(LOG_SHEET);
    sheet.getRange(1, 1, 1, LOG_HEADERS.length).setValues([LOG_HEADERS]);
    sheet.setFrozenRows(1);
  } else if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, LOG_HEADERS.length).setValues([LOG_HEADERS]);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function ensureLogCapacity_(sheet, rowsToAppend) {
  const required = sheet.getLastRow() + rowsToAppend + 500;
  if (sheet.getMaxRows() < required) {
    sheet.insertRowsAfter(sheet.getMaxRows(), required - sheet.getMaxRows());
  }
}
