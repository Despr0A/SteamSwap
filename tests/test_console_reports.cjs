const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const path = require('node:path');

function consoleContext() {
  const controls = Object.fromEntries(Object.entries({
    'log-layer': 'item', 'log-level': 'all', 'log-outcome': 'all', 'log-search': '',
  }).map(([key, value]) => [key, { value }]));
  const output = { dataset: {}, innerHTML: '', querySelectorAll: () => [], scrollHeight: 100 };
  controls['log-output'] = output;
  const context = vm.createContext({
    localStorage: { getItem: () => '0' }, el: id => controls[id],
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/js/console.js'), 'utf8'), context);
  return { controls, output, run: code => vm.runInContext(code, context) };
}

test('report view separates layers, filters outcomes, and follows the selected item', () => {
  const ui = consoleContext();
  ui.run(`logLines = [
    {id: 1, msg: 'item summary', report: {layer:'item', report_id:'first', item:'Item', decision:'reject', checks:[{check:'max_discount', status:'reject', limit:0.7}]}},
    {id: 2, msg: 'cache', report: {layer:'network', report_id:'first', status:'success', source:'database_cache'}},
    {id: 3, msg: 'another', report: {layer:'network', report_id:'second', status:'failed'}},
    {id: 4, msg: 'started', category:'pipeline'}
  ]; renderLogFull();`);
  assert.match(ui.output.innerHTML, /Item/);
  assert.doesNotMatch(ui.output.innerHTML, /database_cache|another|started/);
  ui.run(`showReportNetwork('first')`);
  assert.equal(ui.controls['log-layer'].value, 'network');
  assert.match(ui.output.innerHTML, /本地数据库/);
  assert.doesNotMatch(ui.output.innerHTML, /second|started/);
  ui.controls['log-outcome'].value = 'failed';
  ui.run('renderLogFull()');
  assert.equal(ui.output.innerHTML, '');
});

test('report values and failure diagnostics are escaped and searchable', () => {
  const ui = consoleContext();
  ui.run(`logLines = [{id:1, report:{layer:'item', item:'<img src=x onerror=alert(1)>',
    decision:'reject', checks:[{check:'max_discount',status:'reject',reference_price:20}],
    details:[{message:'<script>danger</script>'}]}, diagnostic:{code:'NETWORK_TEST'}}]; renderLogFull();`);
  assert.doesNotMatch(ui.output.innerHTML, /<img|<script>/);
  assert.match(ui.output.innerHTML, /&lt;img/);
  ui.controls['log-search'].value = 'NETWORK_TEST';
  assert.equal(ui.run('_filteredLogLines().length'), 1);
  ui.controls['log-search'].value = 'missing';
  assert.equal(ui.run('_filteredLogLines().length'), 0);
});

test('polling retains report, flow, and diagnostic fields for download', async () => {
  const ui = consoleContext();
  ui.run(`fetchJson = async () => ({lines:[{id:7, msg:'done', category:'item_report', flow_id:'flow',
    report:{layer:'item',decision:'pass'}, diagnostic:{code:'TEST'}}]}); API='/api';`);
  await ui.run('refreshLog()');
  assert.equal(ui.output.dataset.lastIndex, '7');
  assert.equal(ui.run('logLines[0].flow_id'), 'flow');
  assert.equal(ui.run('logLines[0].diagnostic.code'), 'TEST');
  assert.match(ui.output.innerHTML, /通过/);
});
