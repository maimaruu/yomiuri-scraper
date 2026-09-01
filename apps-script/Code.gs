const SPREADSHEET_ID = '10ka6pLQrFbtw0KNa_nmWIIjDQYLcwEqcGa_HIVnCBOY';
const LATEST_SHEET = 'latest';
const LOG_SHEET = 'tool_logs';

// Script Properties に OPENAI_API_KEY を設定する。
// OPENAI_MODEL は任意。未設定なら gpt-5.4-mini を使う。
const DEFAULT_OPENAI_MODEL = 'gpt-5.4-mini';

// A:O は既存ログとの互換性を維持。P/Q に位置情報を追加。
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
  'metadata_json',
  'source_rank',
  'display_position'
];

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('ニュース観察ツール')
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
      source_rank: Number(row[idx.rank] || 0),
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

/**
 * 一覧または記事画面のチャットから呼ぶ。
 * payload = {
 *   surface: 'list' | 'article',
 *   messages: [{role:'user'|'assistant', content:'...'}],
 *   articles: [...],
 *   current_article_id?: '...'
 * }
 */
function chatWithAI(payload) {
  payload = payload || {};
  const apiKey = PropertiesService.getScriptProperties().getProperty('OPENAI_API_KEY');
  if (!apiKey) {
    throw new Error('OPENAI_API_KEY が未設定です。Apps Script のスクリプト プロパティに設定してください。');
  }

  const model = PropertiesService.getScriptProperties().getProperty('OPENAI_MODEL') || DEFAULT_OPENAI_MODEL;
  const surface = payload.surface === 'article' ? 'article' : 'list';
  const articles = Array.isArray(payload.articles) ? payload.articles : [];
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const currentArticleId = String(payload.current_article_id || '');

  const systemPrompt = buildSystemPrompt_(surface, articles, currentArticleId);
  const input = [
    { role: 'system', content: [{ type: 'input_text', text: systemPrompt }] }
  ];

  messages.slice(-16).forEach(m => {
    const role = m && m.role === 'assistant' ? 'assistant' : 'user';
    const text = String((m && (m.content || m.text)) || '').trim();
    if (!text) return;
    input.push({
      role: role,
      content: [{ type: role === 'assistant' ? 'output_text' : 'input_text', text: text }]
    });
  });

  const requestBody = {
    model: model,
    input: input,
    max_output_tokens: 450
  };

  const response = UrlFetchApp.fetch('https://api.openai.com/v1/responses', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + apiKey
    },
    payload: JSON.stringify(requestBody),
    muteHttpExceptions: true
  });

  const status = response.getResponseCode();
  const raw = response.getContentText();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    throw new Error('OpenAI API の応答を読み取れませんでした。HTTP ' + status);
  }

  if (status < 200 || status >= 300) {
    const message = data && data.error && data.error.message ? data.error.message : raw.slice(0, 500);
    throw new Error('OpenAI API エラー (' + status + '): ' + message);
  }

  const text = extractOutputText_(data).trim();
  if (!text) {
    throw new Error('AIからテキスト応答が返りませんでした。');
  }

  return {
    text: text,
    model: model,
    response_id: data.id || ''
  };
}

function buildSystemPrompt_(surface, articles, currentArticleId) {
  const normalized = articles.slice(0, 5).map((a, i) => {
    const body = String(a.body || '');
    return [
      '【表示位置 ' + (a.display_position || (i + 1)) + '】',
      'タイトル: ' + String(a.title || ''),
      '掲載時刻: ' + String(a.published_at || ''),
      'URL: ' + String(a.url || ''),
      '本文:\n' + body
    ].join('\n');
  });

  if (surface === 'list') {
    return [
      'あなたはニュース一覧を眺める人のための、控えめな対話相手です。',
      'ユーザーには現在、下記5記事の見出し一覧が表示されています。必要なときだけ、この5記事の本文を根拠に答えてください。',
      '',
      '重要な方針:',
      '- ユーザーが聞いたこと・気になったことにだけ答える。',
      '- 頼まれていないのに5記事全部を要約しない。',
      '- 「この記事を読んで」「もっと調べて」など、特定の記事への接触を促さない。',
      '- ユーザーの発言が単なる感想なら、説明を盛らず短く応じる。必要なら「この5本では〜」程度に整理する。',
      '- 今日の出来事について、この5記事にない事実を推測で補わない。情報が足りなければそう言う。',
      '- 難しい用語や背景を聞かれた場合だけ、その理解に必要な最小限を説明する。',
      '- 回答は原則として短く、1〜3段落程度。',
      '',
      '現在表示されている5記事:',
      normalized.join('\n\n')
    ].join('\n');
  }

  const current = articles.find(a => String(a.article_id || '') === currentArticleId) || articles[0] || {};
  return [
    'あなたは、ユーザーがニュース原文を読むことを支える控えめな対話相手です。',
    '目的は記事の代わりになる解説を先回りして与えることではなく、ユーザーが原文を読んで生じた疑問や「わからない」に必要な分だけ答えることです。',
    '',
    '重要な方針:',
    '- 頼まれていないのに記事全体を要約しない。',
    '- ユーザーが実際に尋ねた点・つまずいた点だけを説明する。',
    '- 一度に背景を与えすぎない。まず最小限で答え、追加質問があれば続ける。',
    '- 原文に書かれていることと、一般的な背景説明を混同しない。現在の出来事について原文にない具体的事実を推測しない。',
    '- ユーザーに理解確認問題を出したり、次の記事を勧めたりしない。',
    '- 回答は原則として短く、1〜3段落程度。',
    '',
    '現在読んでいる記事:',
    'タイトル: ' + String(current.title || ''),
    '掲載時刻: ' + String(current.published_at || ''),
    'URL: ' + String(current.url || ''),
    '本文:\n' + String(current.body || '')
  ].join('\n');
}

function extractOutputText_(data) {
  if (data && typeof data.output_text === 'string') return data.output_text;
  const output = data && Array.isArray(data.output) ? data.output : [];
  const texts = [];
  output.forEach(item => {
    const content = item && Array.isArray(item.content) ? item.content : [];
    content.forEach(part => {
      if (part && typeof part.text === 'string' && (part.type === 'output_text' || part.type === 'text')) {
        texts.push(part.text);
      }
    });
  });
  return texts.join('\n');
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

  const sourceRank = payload.source_rank == null ? '' : payload.source_rank;
  const displayPosition = payload.display_position == null ? '' : payload.display_position;

  const row = [
    eventId,
    now,
    payload.client_timestamp || '',
    payload.browser_id || '',
    payload.session_id || '',
    payload.event_type || '',
    payload.article_id || '',
    sourceRank,
    payload.title || '',
    payload.url || '',
    payload.message_index == null ? '' : payload.message_index,
    payload.message_text || '',
    presented.map(a => a.article_id || '').join(' | '),
    presented.map(a => a.title || '').join(' | '),
    JSON.stringify(payload.metadata || {}),
    sourceRank,
    displayPosition
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
  }

  if (sheet.getMaxColumns() < LOG_HEADERS.length) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), LOG_HEADERS.length - sheet.getMaxColumns());
  }

  const existingLastColumn = Math.max(sheet.getLastColumn(), 1);
  const existingHeaders = sheet.getRange(1, 1, 1, existingLastColumn).getValues()[0].map(String);
  if (existingHeaders.every(h => !h)) {
    sheet.getRange(1, 1, 1, LOG_HEADERS.length).setValues([LOG_HEADERS]);
  } else {
    sheet.getRange(1, 16, 1, 2).setValues([['source_rank', 'display_position']]);
  }
  sheet.setFrozenRows(1);
  return sheet;
}

function ensureLogCapacity_(sheet, rowsToAppend) {
  const required = sheet.getLastRow() + rowsToAppend + 500;
  if (sheet.getMaxRows() < required) {
    sheet.insertRowsAfter(sheet.getMaxRows(), required - sheet.getMaxRows());
  }
}
