const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const project = path.resolve(__dirname, '../..');
const live = process.argv.includes('--live');
const artifactsRoot = path.join(project, 'local/validation');
fs.mkdirSync(artifactsRoot, {recursive:true});
const artifacts = fs.mkdtempSync(path.join(artifactsRoot, live ? 'e2e-live-' : 'e2e-replay-'));
const ready = path.join(artifacts, 'ready.json');
const runtime = path.join(artifacts, 'runtime');
const realPointer = path.join(project, 'local/runtime/flood/workspaces/.current.json');
const originalPointer = fs.existsSync(realPointer) ? fs.readFileSync(realPointer,'utf8') : null;
const serverLog = fs.openSync(path.join(artifacts, 'server.log'), 'w');
const server = spawn('uv', ['run','python','tests/e2e/server.py','--runtime-root',runtime,'--ready-file',ready,'--parent-pid',String(process.pid),...(live?['--live']:[])], {
  cwd: project, stdio: ['ignore',serverLog,serverLog], detached: process.platform !== 'win32'
});
const report = {mode:live?'live':'replay',status:'running',started_at:new Date().toISOString(),steps:[]};
const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
let browser, context, page;
function checkpoint(){ fs.writeFileSync(path.join(artifacts,'report.json'),JSON.stringify(report,null,2)); }
for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>{
  report.status='interrupted';checkpoint();
  try{process.kill(process.platform==='win32'?server.pid:-server.pid,'SIGTERM')}catch{}
  process.exit(130);
});
(async()=>{
 try {
  for(let i=0;!fs.existsSync(ready);i++){
    if(server.exitCode!==null)throw Error('Test server exited; see server.log');
    if(i>100)throw Error('Test server did not become ready');
    await pause(100);
  }
  const {url,workspace_id}=JSON.parse(fs.readFileSync(ready,'utf8'));
  report.workspace_id=workspace_id;
  browser=await chromium.launch({headless:true});
  context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.tracing.start({screenshots:true,snapshots:true});
  page=await context.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.route('https://**/*',route=>route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH7sAAAAASUVORK5CYII=','base64')}));
  await page.addInitScript(()=>{
    window.e2eEvents=[];
    const Native=window.EventSource;
    window.EventSource=class extends Native {
      constructor(url,...args){super(url,...args);if(String(url).includes('/api/agent/chat/stream'))for(const name of ['tool_call','tool_result','domain_result','directive_draft','map_actions'])this.addEventListener(name,event=>window.e2eEvents.push(JSON.parse(event.data)));}
    };
  });
  await page.goto(url);await page.click('#enterWorkbenchBtn');
  await page.waitForFunction(()=>typeof state!=='undefined' && state.bootstrap && state.layerGroups.size>=2);
  await page.evaluate(()=>{state.sessionId='e2e-workflows';setAgentDrawerOpen(true);activateAgentPane('chat')});
  const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'scenarios.json')));
  const result=(step,name)=>{
    const event=step.events.filter(e=>e.type==='domain_result'&&e.name===name).at(-1) || step.events.filter(e=>e.type==='tool_result'&&e.name===name).at(-1);
    assert(event,`Missing ${name} result in ${step.id}`);
    const value=typeof event.result==='string'?JSON.parse(event.result):event.result;assert(!value.error,`${name}: ${value.error}`);return value;
  };
  async function ask(id){
    const scenario=cases.find(item=>item.id===id);
    await page.evaluate(()=>window.e2eEvents=[]);
    await page.fill('#chatInput',scenario.message);
    await page.locator('#chatForm button[type="submit"]').click();
    await page.waitForFunction(()=>window.e2eEvents.length>0 && !state.activeStream,{},{timeout:120000});
    await page.evaluate(()=>state.mapActionQueue);
    const step={id,message:scenario.message,...await page.evaluate(()=>({events:window.e2eEvents,reply:document.getElementById('chatLog').lastElementChild?.textContent,receipts:[...state.mapActionReceipts.values()].slice(-3)}))};
    report.steps.push(step);checkpoint();
    assert(!step.events.some(e=>e.name==='run_flood_forecast'),`${id} unexpectedly recomputed forecast`);
    console.log(`completed ${id}`);return step;
  }
  const visible = type=>page.evaluate(type=>[...visibleObjectIds(type)].sort(),type);
  async function selectHour(hour){
    await page.locator('#hydroTimeSlider').fill(String(await page.evaluate(hour=>state.hydrodynamicTimeline.hours.indexOf(hour),hour)));
    await page.locator('#hydroTimeSlider').dispatchEvent('change');
    await page.waitForFunction(hour=>currentHydrodynamicTimelineContext().current_hydrodynamic_time_h===hour,hour);
  }
  const unrelatedSite='shelter_232';
  await page.evaluate(id=>loadObject('EvacuationSite',{}, {objectIds:[id],fit:false,label:'其他已显示安置点'}),unrelatedSite);
  const nearby=await ask('nearby');
  const near=result(nearby,'find_nearby_objects');
  const originalIds=await visible('EvacuationSite');
  assert(originalIds.includes(unrelatedSite));
  assert.equal(originalIds.length,near.total_matched+1);
  const filtered=result(await ask('capacity'),'refine_object_set');
  const allFilteredIds=await visible('EvacuationSite');
  assert(allFilteredIds.includes(unrelatedSite),'Filtering removed an unrelated displayed object');
  const filteredIds=allFilteredIds.filter(id=>id!==unrelatedSite);
  assert(filteredIds.length>0);assert.equal(filteredIds.length,filtered.count);
  assert(filteredIds.every(id=>originalIds.includes(id)));
  const options=result(await ask('compare'),'compare_evacuation_sites');
  assert.equal(options.required_capacity,163);assert.equal(options.route_checked,false);
  assert(options.candidates.filter(c=>c.eligible).every(c=>c.capacity_person>=163));
  const route=result(await ask('route'),'plan_route');
  assert.equal(route.route.destination_site_id,options.recommended_site_id);
  assert.equal(route.route.profile,'foot');assert.equal(route.route.flood_validation,'initial_dry');
  assert((await visible('EvacuationRoute')).includes(route.route.evacuation_route_id));
  assert.equal(result(await ask('review'),'review_route').passable,true);
  await ask('draft');
  await page.locator('#directiveDraftToast').waitFor({state:'visible'});
  let issued=await (await page.request.get(url+'/api/directives')).json();
  assert.equal(issued.directives.length,0,'Draft was published without a user action');
  const draft=await page.evaluate(()=>state.directiveDraft);
  assert.equal(draft.basis,undefined);
  await page.fill('#directiveTitle','端到端验证：平竹村转移准备');
  await page.click('#directiveIssueBtn');
  await page.waitForFunction(()=>state.directives.length===1);
  issued=await (await page.request.get(url+'/api/directives')).json();
  const immutable=JSON.stringify(issued.directives[0]);
  assert.equal(issued.directives[0].basis,undefined);
  assert.equal(issued.directives[0].title,'端到端验证：平竹村转移准备');
  await page.screenshot({path:path.join(artifacts,'issued.png')});

  // A fixture forecast activates the actual grid/impact/deadline code, without running CNN.
  const seeded=await page.request.post(url+'/__test__/forecast',{data:{wet_now:false}});assert(seeded.ok());
  await page.locator('#hydroTimeline').waitFor({state:'visible'});
  await page.waitForFunction(()=>state.hydrodynamicTimeline.hours.length===48);
  assert.equal(await page.locator('#hydroTimeSlider').isEnabled(),true);
  const autoTimeline=await page.evaluate(()=>currentHydrodynamicTimelineContext());
  assert.equal(autoTimeline.mode,'time_slice');assert.equal(autoTimeline.current_hydrodynamic_time_h,0.5);
  assert.equal(await page.evaluate(()=>state.hydrodynamicTimeline.hours.at(-1)),24);
  await page.locator('#hydroTimeSlider').focus();await page.keyboard.press('End');
  await page.waitForFunction(()=>currentHydrodynamicTimelineContext().current_hydrodynamic_time_h===24);
  const impact=result(await ask('forecast_scope'),'analyze_inundation_impacts');
  assert.deepEqual(impact.analysis_scope.matched_object_ids.EvacuationSite.sort(),filteredIds);
  assert.equal(impact.time_h,6);
  const originParams=new URLSearchParams({forecast_id:'v001',target_type:'EvacuationUnit',object_ids:JSON.stringify(['43']),time_h:'6'});
  const originImpact=await (await page.request.get(url+'/api/impact-analysis?'+originParams)).json();
  assert.equal(originImpact.total_impacts,1);
  assert.equal(originImpact.impacts[0].velocity_source,'depth_estimate');
  const evidenceHtml=await page.evaluate(item=>impactPopupHtml(item),originImpact.impacts[0]);
  assert(evidenceHtml.includes('估算流速'));
  assert.equal(await page.evaluate(()=>formatImpactNumber(null,2)),'--');
  const uiImpact=await page.evaluate(()=>({ids:state.impactAnalysis?.objectIds,hour:currentHydrodynamicTimelineContext().current_hydrodynamic_time_h}));
  assert.deepEqual(uiImpact.ids.sort(),filteredIds);assert.equal(uiImpact.hour,6);
  const deadline=result(await ask('deadline'),'analyze_latest_evacuation_time');
  assert.equal(deadline.status,'completed');
  assert.equal(deadline.evacuation_route.evacuation_route_id,route.route.evacuation_route_id);
  assert.equal(deadline.deadline.first_unsafe_time_h,6);
  assert.equal(deadline.parameters.blocked_depth_m,route.route.blocked_depth_m);
  report.deadline=deadline;
  await page.screenshot({path:path.join(artifacts,'forecast.png')});

  // The same route becomes blocked in the updated forecast; issued records stay immutable.
  assert((await page.request.post(url+'/__test__/forecast',{data:{wet_now:true}})).ok());
  await page.waitForFunction(()=>currentHydrodynamicTimelineContext().forecast_version==='v002');
  await selectHour(1);
  const issuedReviewStep=await ask('review_issued');
  assert(issuedReviewStep.events.some(e=>e.type==='tool_call'&&e.name==='query'&&e.args?.object_type==='EmergencyDirective'),
    'Agent must query issued records instead of inferring issuance from earlier chat');
  const review=result(issuedReviewStep,'review_route');
  assert.equal(review.passable,false);assert.equal(review.evacuation_route_id,route.route.evacuation_route_id);
  // A new draft can still be opened when a separate route review failed.
  assert((await ask('draft_blocked')).events.some(e=>e.type==='directive_draft'),
    'A blocked route must not prevent drafting the requested response');
  await page.locator('#directiveDraftToast').waitFor({state:'visible'});
  assert.equal((await (await page.request.get(url+'/api/directives')).json()).directives.length,1);
  await page.click('#directiveCancelBtn');
  await page.evaluate(()=>setTelemetryPanelOpen(true));
  await page.locator('[data-view-directive]').first().click();
  assert.equal(await page.locator('#directiveContent').evaluate(element=>element.readOnly),true);
  await page.click('#directiveCopyBtn');
  await page.fill('#directiveTitle','端到端验证：路线受阻后的处置');
  await page.fill('#directiveContent','请现场核查受阻路线并准备替代转移方案。');
  assert.equal(await page.evaluate(()=>state.directiveDraft.basis),undefined);
  await page.click('#directiveIssueBtn');
  await page.waitForFunction(()=>state.directives.length===2);
  issued=await (await page.request.get(url+'/api/directives')).json();
  assert.equal(issued.directives.length,2);assert.equal(JSON.stringify(issued.directives[1]),immutable);
  assert.equal(issued.directives[0].title,'端到端验证：路线受阻后的处置');
  assert.equal(issued.directives[0].forecast_version,'v002');
  assert.equal(issued.directives[0].simulation_time,'2026-07-03T09:00:00+08:00');
  assert.equal(issued.directives[0].basis,undefined);
  await page.screenshot({path:path.join(artifacts,'copied-draft.png')});

  // A real reservoir snapshot supports an independent t0 trial. CNN output is deterministic.
  const dispatchSeed=await page.request.post(url+'/__test__/dispatch-forecast',{data:{}});assert(dispatchSeed.ok());
  const dispatchMetadata=await dispatchSeed.json();
  await page.waitForFunction(()=>currentHydrodynamicTimelineContext().forecast_version==='v003');
  await selectHour(0.5);
  const early=result(await ask('current_roads'),'analyze_inundation_impacts');
  assert.equal(early.forecast_id,'v003');assert.equal(early.time_h,0.5);assert.equal(early.total_impacts,0);
  await selectHour(12);
  const later=result(await ask('current_roads'),'analyze_inundation_impacts');
  assert.equal(later.forecast_id,'v003');assert.equal(later.time_h,12);assert(later.total_impacts>0);
  function formalState(){
    const root=path.join(runtime,'workspaces',workspace_id);
    const files={};
    for(const sub of ['forecasts','boundary_flows']){
      const directory=path.join(root,sub);
      for(const entry of fs.readdirSync(directory,{recursive:true,withFileTypes:true}))if(entry.isFile()){
        const file=path.join(entry.parentPath||entry.path,entry.name);
        files[path.relative(root,file)]=createHash('sha256').update(fs.readFileSync(file)).digest('hex');
      }
    }
    return files;
  }
  const beforeTrial=formalState();
  const trialStep=await ask('dispatch_trial');
  const plan=result(trialStep,'get_longtan_dispatch_plan');
  const trial=result(trialStep,'simulate_longtan_dispatch');
  assert.equal(plan.t0,dispatchMetadata.valid_from);
  assert.equal(trial.status,'completed');assert.equal(trial.applied,false);
  assert.equal(trial.t0,dispatchMetadata.valid_from);assert.equal(trial.time_h,12);
  assert.equal(trial.baseline_forecast_id,'v003');assert.equal(trial.horizon_hours,24);
  assert.equal(trial.candidate_settings.mode,'OUTFLOW');assert.equal(trial.candidate_settings.target_outflow_m3s,2);
  assert.equal(trial.reservoir_safety.candidate.passed,true);
  for(const view of ['selected_time','window_envelope']){
    assert(trial.comparison[view].baseline.affected_count>0);
    assert.equal(trial.comparison[view].candidate.affected_count,0);
  }
  assert.deepEqual(formalState(),beforeTrial,'Trial changed a formal prediction or boundary input');
  assert.equal(await page.evaluate(()=>currentHydrodynamicTimelineContext().current_hydrodynamic_time_h),12);
  report.dispatch_trial=trial;
  await page.screenshot({path:path.join(artifacts,'dispatch-trial.png')});

  const reset=await page.request.post(url+'/api/autonomy/reset',{data:{speed_multiplier:1}});assert(reset.ok());
  const resetData=await reset.json();
  await page.waitForFunction(id=>state.workspaceId===id,resetData.workspace_id);
  assert.equal((await visible('EvacuationRoute')).length,0);
  const stale=await page.request.post(url+'/api/directives',{data:{workspace_id,title:'old',content:'old',recipients:'old'}});
  assert.equal(stale.status(),400);
  assert.equal((await (await page.request.get(url+'/api/directives')).json()).directives.length,0);
  assert.deepEqual(errors,[]);
  report.status='passed';
 } catch(error){
  report.status='failed';report.error=error.stack;
  if(page)await page.screenshot({path:path.join(artifacts,'failure.png')}).catch(()=>{});
  console.error(error);process.exitCode=1;
 } finally {
  if(context)await context.tracing.stop({path:path.join(artifacts,'browser-trace.zip')}).catch(()=>{});
  if(browser)await browser.close();
  try{process.kill(process.platform==='win32'?server.pid:-server.pid,'SIGTERM')}catch{}
  fs.closeSync(serverLog);
  report.finished_at=new Date().toISOString();
  const pointer=fs.existsSync(realPointer)?fs.readFileSync(realPointer,'utf8'):null;
  if(pointer!==originalPointer){report.status='failed';report.isolation_error='Current production workspace changed';process.exitCode=1;}
  fs.writeFileSync(path.join(artifacts,'report.json'),JSON.stringify(report,null,2));
  console.log(`${report.status}: ${path.relative(project,artifacts)}/report.json`);
 }
})();
