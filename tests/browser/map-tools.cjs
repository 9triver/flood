// Run with NODE_PATH=local/validation/node_modules node tests/browser/map-tools.cjs
const {chromium} = require('playwright');
const {execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const fixtures = JSON.parse(execFileSync('uv', ['run', 'python', '-c', `
import json,sys
sys.path.insert(0,'agent')
from oag.ontology.schema import Ontology
from server.presentation.map_actions import MapActionBuilder
from domains.flood.runtime.repository import FloodRepository
b=MapActionBuilder(Ontology.load('domains/flood/ontology.yaml'),FloodRepository())
def show(ids,**extra):
 return json.loads(b.show_objects({'objects':[{'object_type':'EvacuationSite','object_ids':ids,'fit':False,**extra}]},['EvacuationSite']))
print(json.dumps({'one':show(['shelter_235'],highlight=True),'two':show(['shelter_232']), 'empty':show([],mode='replace'),'replace':json.loads(b.show_objects({'objects':[{'object_type':'EvacuationSite','object_ids':['shelter_235'],'mode':'replace','fit':False},{'object_type':'EvacuationSite','object_ids':['shelter_232'],'mode':'replace','fit':False}]},['EvacuationSite']))}))
`], {encoding:'utf8'}));
(async()=>{
 const browser = await chromium.launch({headless:true});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.EventSource=class{addEventListener(){} close(){}}});
  await page.goto(process.env.TEST_BASE_URL || 'http://127.0.0.1:8765');
  await page.click('#enterWorkbenchBtn');
  await page.waitForFunction(()=>state.bootstrap && state.layerGroups.size>=2);
  await page.waitForTimeout(600);
  const execute = data=>page.evaluate(data=>enqueueMapActions(data),data);
  const ids = ()=>page.evaluate(()=>[...visibleObjectIds('EvacuationSite')].sort());
  await execute(fixtures.one);assert.deepEqual(await ids(),['shelter_235']);
  await execute(fixtures.two);assert.deepEqual(await ids(),['shelter_232','shelter_235']);
  const center = await page.evaluate(()=>JSON.stringify(state.map.getCenter()));
  await execute(fixtures.empty);assert.equal((await ids()).length,2);
  await execute({operation_id:'hide-one',map_actions:[{type:'hide_objects',selection_id:fixtures.one.selections[0].selection_id}]});
  assert.deepEqual(await ids(),['shelter_232']);
  assert.equal(await page.evaluate(()=>JSON.stringify(state.map.getCenter())),center);
  assert(await page.evaluate(()=>[...state.layerMeta.values()].some(meta=>meta.objectType==='River')));
  assert.equal(await page.evaluate(id=>state.mapActionReceipts.get(id).actions[0].removed_count,'hide-one'),1);
  // A partial hide affects every layer containing the target; later reload restores it.
  await page.evaluate(()=>loadObject('EvacuationSite',{}, {fit:false}));
  const fullCount=(await ids()).length;assert(fullCount>2);
  await page.evaluate(id=>hideMapObjects({selection_id:id}),fixtures.one.selections[0].selection_id);
  assert.equal((await ids()).length,fullCount-1);
  await page.evaluate(()=>loadObject('EvacuationSite',{}, {fit:false}));
  assert.equal((await ids()).length,fullCount);
  await execute(fixtures.replace);assert.deepEqual(await ids(),['shelter_232','shelter_235']);
  const selections=await page.evaluate(()=>frontendAgentContext().visible_selections);
  assert(selections.some(s=>s.selection_id===fixtures.replace.selections[1].selection_id && s.count===1));
  // Failed replacement retains the already visible objects.
  await execute({operation_id:'bad-replace',map_actions:[{type:'load_object',object_type:'EvacuationSite',object_ids:['unknown'],mode:'replace'}]});
  assert.equal(await page.evaluate(()=>state.mapActionReceipts.get('bad-replace').status),'failed');
  assert.deepEqual(await ids(),['shelter_232','shelter_235']);
  // Focus only loads its requested object and rejects missing targets without moving the map.
  await page.evaluate(()=>removeObjectTypeLayers('EvacuationSite'));
  await page.evaluate(()=>focusObject({object_type:'EvacuationSite',object_id:'shelter_235'}));
  assert.deepEqual(await ids(),['shelter_235']);
  await page.evaluate(()=>state.map.stop());
  const focusedCenter=await page.evaluate(()=>JSON.stringify(state.map.getCenter()));
  await execute({operation_id:'bad-focus',map_actions:[{type:'focus_object',object_type:'EvacuationSite',object_id:'unknown'}]});
  assert.equal(await page.evaluate(()=>JSON.stringify(state.map.getCenter())),focusedCenter);
  // Deterministic forecast metadata, no model run and no workspace changes.
  await page.route('**/api/hydrodynamic-grid/meta?*',route=>route.fulfill({json:{feature_count:100,min_tile_zoom:13,bbox:{min_lon:111.1,min_lat:24.3,max_lon:111.2,max_lat:24.5},forecast:{forecast_id:'test',depth_path:'test.csv',series_path:'test.npy',result_version:'vtest',time_steps_h:[0,6,12],valid_from:'2026-10-04T00:00:00Z'}}}));
  await page.route('**/api/hydrodynamic-grid/tile?*',route=>route.fulfill({json:{cells:[]}}));
  await page.evaluate(()=>applyHydrodynamicResult({filters:{forecast_id:'test',time_h:6},fit:true}));
  assert.equal(await page.evaluate(()=>currentHydrodynamicTimelineContext().current_hydrodynamic_time_h),6);
  await page.evaluate(()=>executeActions([{type:'show_hydrodynamic_mesh',mesh_only:false,fit:false}]));
  assert.equal(await page.evaluate(()=>currentHydrodynamicTimelineContext().current_hydrodynamic_time_h),6);
  await page.evaluate(()=>executeActions([{type:'hide_hydrodynamic_mesh'}]));
  assert.equal(await page.evaluate(()=>currentHydrodynamicTimelineContext().current_hydrodynamic_time_h),6);
  await page.evaluate(()=>applyHydrodynamicResult({filters:{forecast_id:'test',view:'envelope'}}));
  assert.equal(await page.evaluate(()=>currentHydrodynamicTimelineContext().mode),'envelope');
  await execute({operation_id:'bad-time',map_actions:[{type:'apply_hydrodynamic_result',filters:{forecast_id:'test',time_h:99}}]});
  assert.equal(await page.evaluate(()=>state.mapActionReceipts.get('bad-time').status),'failed');
  assert.equal(await page.evaluate(()=>currentHydrodynamicTimelineContext().mode),'envelope');
  // A time change must preserve the selected IDs, attribute filters and thresholds.
  let impactQuery;
  await page.route('**/api/impact-analysis?*', async route => {
    impactQuery = new URL(route.request().url()).searchParams;
    await route.fulfill({json:{status:'completed',forecast_id:'test',time_h:null,target_type:'EvacuationSite',
      analysis_scope:{mode:'selected',requested_object_ids:['shelter_235'],filters:{site_type:'shelter'},matched_count:1},
      parameters:{min_depth_m:0.3,max_distance_m:25,bridge_influence_radius_m:80},impacts:[],nearby_impacts:[],total_impacts:0}});
  });
  await page.evaluate(()=>{
    registerImpactAnalysisResult({status:'completed',forecast_id:'test',time_h:null,target_type:'EvacuationSite',
      analysis_scope:{mode:'selected',requested_object_ids:['shelter_235'],filters:{site_type:'shelter'},matched_count:1},
      parameters:{min_depth_m:0.3,max_distance_m:25}}, {render:false});
    window.clearTimeout(state.impactRefreshTimer);
    return refreshImpactAnalysisForTimeline();
  });
  assert.equal(impactQuery.get('target_type'),'EvacuationSite');
  assert.deepEqual(JSON.parse(impactQuery.get('object_ids')),['shelter_235']);
  assert.deepEqual(JSON.parse(impactQuery.get('filters')),{site_type:'shelter'});
  assert.equal(impactQuery.get('min_depth_m'),'0.3');
  assert.equal(impactQuery.get('max_distance_m'),'25');
  // History reconnect cannot repeat an already executed operation.
  await execute(fixtures.replace);
  assert.deepEqual(await ids(),['shelter_235']);
  assert.deepEqual(errors,[]);
  console.log('PASS scoped show/hide, empty set, additive groups, explicit replace, focus, failure preservation, context/receipts, forecast time/envelope and mesh overlay, replay dedupe');
 } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1)});
