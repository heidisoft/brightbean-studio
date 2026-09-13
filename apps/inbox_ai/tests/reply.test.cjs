const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function component(fetch) {
    let factory;
    const sandbox = {
        document: {addEventListener: (_name, callback) => callback()},
        Alpine: {data: (_name, create) => { factory = create; }},
        AbortController, URLSearchParams, setTimeout, clearTimeout, fetch,
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/inbox_ai/reply.js'), 'utf8'), sandbox);
    return Object.assign(factory(), {
        // Alpine's $el is the clicked style button, while $root owns the URL.
        $el: {dataset: {}, closest: () => ({querySelector: () => ({value: 'csrf-token'})})},
        $root: {dataset: {generateUrl: '/workspace/test/inbox/ai/message/generate/'}},
    });
}

function response(data, status = 200) {
    return new Response(JSON.stringify(data), {status, headers: {'Content-Type': 'application/json'}});
}

test('uses the component URL and includes article and CSRF without touching the draft', async () => {
    let request;
    const state = component(async (url, options) => {
        request = {url, options};
        return response({reply: 'ලිපිය අනුව ඔව්.'});
    });
    state.replyText = 'Existing draft';
    state.article = 'සම්පූර්ණ ලිපිය';
    await state.generate('factual');
    assert.equal(request.url, state.$root.dataset.generateUrl);
    assert.equal(request.options.headers['X-CSRFToken'], 'csrf-token');
    assert.equal(request.options.body.get('article_text'), state.article);
    assert.equal(request.options.body.get('style'), 'factual');
    assert.equal(state.suggestion, 'ලිපිය අනුව ඔව්.');
    assert.equal(state.replyText, 'Existing draft');
    assert.equal(state.busy, false);
});

test('failure keeps the previous suggestion and allows retry', async () => {
    const state = component(async () => response({error: 'Try again later'}, 503));
    state.suggestion = 'Previous suggestion';
    await state.generate('friendly');
    assert.equal(state.suggestion, 'Previous suggestion');
    assert.equal(state.error, 'Try again later');
    assert.equal(state.busy, false);
});

test('blocks duplicate clicks and aborts when the panel is removed', async () => {
    let calls = 0;
    let signal;
    let complete;
    const state = component((_url, options) => {
        calls++;
        signal = options.signal;
        return new Promise(resolve => { complete = resolve; });
    });
    const first = state.generate('factual');
    await state.generate('short');
    assert.equal(calls, 1);
    state.destroy();
    assert.equal(signal.aborted, true);
    complete(response({reply: 'Test'}));
    await first;
});

test('handles expired sessions and empty output without losing suggestions', async () => {
    for (const reply of [response({}, 403), response({reply: ''})]) {
        const state = component(async () => reply);
        state.suggestion = 'Keep me';
        await state.generate('short');
        assert.ok(state.error);
        assert.equal(state.suggestion, 'Keep me');
    }
});
