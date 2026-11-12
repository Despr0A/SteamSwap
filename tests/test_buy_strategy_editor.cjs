const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

function editor() {
  const context = vm.createContext({
    window: { addEventListener() {} },
    document: { addEventListener() {} },
    toast() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/js/strategies.js'), 'utf8'), context);
  const run = code => vm.runInContext(code, context);
  run(`
    renderStrategies = () => {};
    strategiesState.modules = [
      ...STRATEGY_BUY_FIXED_MODULES.map(id => ({id, strategy_types: ['buy']})),
      {id: 'guard.volatility_cv', strategy_types: ['buy'], conflicts: ['guard.history_stability']},
      {id: 'guard.history_stability', strategy_types: ['buy'], conflicts: ['guard.volatility_cv']},
    ];
    strategiesState.draft = {origin: 'custom', strategy_type: 'buy', steps: strategyBlankSteps()};
  `);
  return run;
}

test('buy editor adds and removes optional checks without losing price and payment steps', () => {
  const run = editor();
  assert.equal(run(`addStep('guard.volatility_cv')`), true);
  const ids = JSON.parse(run(`JSON.stringify(strategiesState.draft.steps.map(step => step.module_id))`));
  assert.ok(ids.indexOf('buy.steam_sell_depth') < ids.indexOf('guard.volatility_cv'));
  assert.ok(ids.indexOf('guard.volatility_cv') < ids.indexOf('guard.target_balance'));
  assert.equal(ids.at(-1), 'action.buff_lock_pay');
  run(`deleteStep(0); toggleStep(0);`);
  assert.equal(run(`strategiesState.draft.steps[0].module_id`), 'buy.steamdt_top_n');
  assert.equal(run(`strategiesState.draft.steps[0].enabled`), true);
  run(`deleteStep(strategiesState.draft.steps.findIndex(step => step.module_id === 'guard.volatility_cv'))`);
  assert.equal(run(`strategiesState.draft.steps.length`), 6);
});

test('buy editor still rejects duplicate and conflicting checks', () => {
  const run = editor();
  assert.equal(run(`addStep('guard.volatility_cv')`), true);
  assert.equal(run(`addStep('guard.volatility_cv')`), false);
  assert.equal(run(`addStep('guard.history_stability')`), false);
  assert.equal(run(`strategiesState.draft.steps.length`), 7);
});
