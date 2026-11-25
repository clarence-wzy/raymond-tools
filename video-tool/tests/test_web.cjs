const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function fixture(saveFails = false) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', checked: false, hidden: false, disabled: false,
      classList: { toggle() {} }, focus() {},
    });
    return elements.get(id);
  };
  const requests = [];
  const context = vm.createContext({
    document: {
      getElementById: element,
      querySelector: () => ({ value: 'auto' }),
    },
    URL, console, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      const failed = saveFails && url.endsWith('/settings');
      return { ok: !failed, json: async () => failed
        ? { error: '无法保存设置' }
        : { jobs: [{}], results: [{ index: 0, status: 'created', output: 'video.mp4' }] } };
    },
  });
  const source = fs.readFileSync(path.join(__dirname, '../video_downloader/web/app.js'), 'utf8');
  vm.runInContext(source.split('$("start").addEventListener')[0], context);
  const rendered = [];
  context.recordBatchResult = (rows, results) => rows.forEach((row, index) => {
    rendered.push([row.line, results[index].status]);
  });
  vm.runInContext('refresh = async () => {}; syncUrlState = () => {}; showBatchResult = recordBatchResult;', context);
  element('url').value = 'https://example.com/video.m3u8';
  element('proxy-mode').value = 'direct';
  element('max-concurrent').value = '2';
  element('hls-workers').value = '4';
  element('native-hls').checked = true;
  return { context, element, requests, rendered };
}

test('unchecked strict mode is saved before creating a download', async () => {
  const { context, element, requests } = fixture();
  element('strict-errors').checked = false;
  await context.startDownload();
  assert.deepEqual(requests.map(request => request.url), [
    '/api/video_downloader/settings', '/api/video_downloader/start_batch',
  ]);
  assert.equal(requests[0].body.strict_errors, false);
  assert.equal(requests[0].body.native_hls, true);
});

test('failed settings save prevents a download under stale settings', async () => {
  const { context, element, requests } = fixture(true);
  await context.startDownload();
  assert.equal(requests.length, 1);
  assert.equal(element('url').value, 'https://example.com/video.m3u8');
  assert.equal(element('notice').textContent, '无法保存设置');
});

test('retry submits the original task directly without replacing a draft', async () => {
  const { context, element, requests } = fixture();
  element('url').value = 'https://example.com/unsent-draft.m3u8';
  await context.retryJob('original-id');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/video_downloader/retry');
  assert.equal(requests[0].body.id, 'original-id');
  assert.equal(element('url').value, 'https://example.com/unsent-draft.m3u8');
});

test('mixed batches submit only valid unique addresses and retain invalid lines', async () => {
  const { context, element, requests, rendered } = fixture();
  const good = 'https://example.com/video.m3u8';
  element('url').value = [good, '', good, 'invalid address'].join('\n');
  context.updateSubmitState();
  assert.equal(element('url-count').textContent, '1 个有效 · 1 个无效 · 1 个重复');
  await context.startDownload();
  assert.deepEqual(requests[1].body.urls, [good]);
  assert.deepEqual(rendered, [[1, 'created'], [3, 'duplicate'], [4, 'invalid']]);
  assert.equal(element('url').value, 'invalid address');
  assert.equal(element('start').disabled, true);
});
