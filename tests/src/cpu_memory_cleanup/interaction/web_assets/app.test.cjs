const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../../../../src/cpu_memory_cleanup/interaction/web_assets/app.js'), 'utf8');

function page(reduced = false) {
  const elements = {}, frames = new Map(); let next = 0,clock=0;
  function element() {
    return {value:'', textContent:'', hidden:true, handlers:{}, attrs:{}, children:[],
      style:{setProperty(k,v){this[k]=v;}}, classes:new Set(),
      setAttribute(k,v){this.attrs[k]=v;}, removeAttribute(k){delete this.attrs[k];},
      addEventListener(k,v){this.handlers[k]=v;}, focus(){},
      replaceChildren(){this.children=[];}, append(...items){this.children.push(...items);},
      get classList(){const self=this;return {toggle(k,on){on?self.classes.add(k):self.classes.delete(k);}};}};
  }
  const context = vm.createContext({document:{hidden:false,body:{style:{}},
    getElementById(id){return elements[id]??=element();},createElement:element,addEventListener(){}},
    window:{addEventListener(){}}, matchMedia:()=>({matches:reduced}),performance:{now:()=>clock},
    requestAnimationFrame(fn){frames.set(++next,fn);return next;},cancelAnimationFrame(id){frames.delete(id);},
    setTimeout(){return 0;},clearTimeout(){},fetch:()=>new Promise(()=>{})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../../../../../src/cpu_memory_cleanup/interaction/web_assets/cleanup-effect.js"),"utf8"),context);
  vm.runInContext(source,context);
  return {elements,run:s=>vm.runInContext(s,context),tick(time){clock=time;const jobs=[...frames.values()];frames.clear();jobs.forEach(fn=>fn(time));},frames};
}
const snapshot = available => `({samples:[{metrics:{physical:{total_bytes:100,available_bytes:${available}},commit:{used_bytes:10,limit_bytes:200}}}]})`;

test('25/75 occupancy updates actual ring, number and accessible bar together',()=>{
  const p=page();
  for(const [available,pct,angle] of [[75,25,90],[25,75,270]]){
    p.run(`paintMemory({total:100,available:${available}})`);
    assert.equal(p.elements.percent.textContent,`${pct}%`);
    assert.equal(p.elements.gauge.style['--usage'],`${angle}deg`);
    assert.equal(p.elements.meter.attrs['aria-valuenow'],String(pct));
    assert.equal(p.elements.fill.style.width,`${pct}%`);
    assert.equal(p.elements.meter.style['--usage'],undefined);
  }
});
test('business busy indicator disables repeated main clicks',()=>{
  const p=page();p.run('token="test";supportedMethods=["working-set"];busy=true;cleaning=true;el("method").value="working-set";controls()');
  assert.ok(p.elements.gauge.classes.has('working'));
  assert.ok(!p.elements.meter?.classes.has('working'));
  assert.equal(p.elements['global-clean'].disabled,true);
  assert.equal(p.elements['global-clean'].textContent,'正在整理…');
});
test('interpolation and reduced motion preserve final observed occupancy',()=>{
  const p=page();p.run(`memory(${snapshot(75)})`);p.run(`memory(${snapshot(25)})`);
  p.tick(200);assert.equal(p.elements.percent.textContent,'50%');
  p.tick(400);assert.equal(p.elements.percent.textContent,'75%');assert.equal(p.frames.size,0);
  const q=page(true);q.run(`memory(${snapshot(75)})`);q.run(`memory(${snapshot(25)})`);
  assert.equal(q.elements.percent.textContent,'75%');assert.equal(q.frames.size,0);
});
test('working-set is direct; advanced method still confirms its scope',()=>{
  const p=page();p.run('var actions=[];run=action=>actions.push(action);el("method").value="working-set"');
  p.elements['global-clean'].handlers.click();
  assert.equal(p.run('actions.length'),1);assert.equal(p.run('actions[0].mechanism'),'working-set');
  assert.equal(p.elements.confirmation,undefined);
  p.elements.method.value='standby';p.elements['global-clean'].handlers.click();
  assert.equal(p.run('actions.length'),1);assert.equal(p.elements.confirmation.hidden,false);
  assert.match(p.elements['confirmation-text'].textContent,/Standby/);
});
test('live updates leave recent action feedback and original report intact',()=>{
  const p=page();p.run('present({kind:"cleanup",report:{completion:"success",errors:[],samples:[],action:{call_outcome:"success"}},presentation:{title:"完成 · 可用内存增加 0.20 GiB",note:"前后值",context:"系统工作集 · 08:00"}})');
  const report=p.elements.report.textContent;
  p.run(`memory(${snapshot(75)})`);p.run(`memory(${snapshot(25)})`);p.tick(400);
  assert.equal(p.elements['recent-title'].textContent,'完成 · 可用内存增加 0.20 GiB');
  assert.equal(p.elements['recent-context'].textContent,'系统工作集 · 08:00');
  assert.equal(p.elements.report.textContent,report);
});

test('without effect, live occupancy continues updating during cleanup',()=>{
  const p=page();p.run('busy=true;cleaning=true');
  p.run(`memory(${snapshot(60)})`);
  p.run(`memory(${snapshot(70)})`);p.tick(400);
  assert.equal(p.elements.percent.textContent,'30%');
  assert.equal(p.elements.gauge.style['--usage'],'108deg');
  assert.equal(p.elements.meter.attrs['aria-valuenow'],'30');
  p.run(`memory(${snapshot(50)})`);p.tick(800);
  assert.equal(p.elements.percent.textContent,'50%');
  assert.equal(p.elements.gauge.style['--usage'],'180deg');
  assert.equal(p.elements.meter.attrs['aria-valuenow'],'50');
});


test('decoration enabled or disabled leaves action, report and live values equivalent',async()=>{
  const results=[];
  for(const enabled of [true,false]){
    const p=page();p.elements['decoration-enabled'].checked=enabled;
    await p.run(`token='session';supportedMethods=['working-set'];el('method').value='working-set';
      var actionCount=0;var original={completion:'success',errors:[],samples:[],action:{call_outcome:'success'}};
      api=async path=>{if(path==='/api/action'){actionCount++;return {id:'one'};}return {id:'one',kind:'cleanup',state:'finished',report:original,presentation:{title:'实际结果',note:'快照结果',context:'记录'}};};
      memory(${snapshot(60)});run({kind:'cleanup',mechanism:'working-set'});`);
    assert.equal(p.elements['global-clean'].disabled,false);
    assert.equal(p.elements['recent-title'].textContent,'实际结果');
    assert.equal(p.run('decoration.phase'),enabled?'emptying':null);
    p.run(`memory(${snapshot(70)})`);p.tick(400);
    assert.equal(p.elements['actual-percent'].textContent,'30%');
    if(!enabled)assert.equal(p.elements.percent.textContent,'30%');
    else assert.equal(p.elements['gauge-label'].textContent,'正在整理');
    results.push(p.run('JSON.stringify([actionCount,original])'));
  }
  assert.equal(results[0],results[1]);
});
test('decoration failure cannot fail the business request',async()=>{
  const p=page();p.elements['decoration-enabled'].checked=true;
  await p.run(`token='session';supportedMethods=['working-set'];el('method').value='working-set';
    decoration.start=()=>{throw new Error('visual only')};
    api=async path=>path==='/api/action'?{id:'one'}:{id:'one',kind:'cleanup',state:'finished',report:{completion:'success',errors:[],samples:[],action:{call_outcome:'success'}},presentation:{title:'完成',note:'原报告',context:'时间'}};
    run({kind:'cleanup',mechanism:'working-set'});`);
  assert.equal(p.elements['recent-title'].textContent,'完成');
  assert.equal(p.elements['global-clean'].disabled,false);
});


test('linear circle waits for business and new quiet readings then retargets continuously',()=>{
 const p=page();p.run(`memory(${snapshot(60)});decoration.start(40)`);
 p.tick(1500);assert.equal(p.elements.percent.textContent,'20%');
 p.run(`memory(${snapshot(70)})`);p.tick(3000);
 assert.equal(p.elements.percent.textContent,'0%');
 assert.equal(p.elements['actual-percent'].textContent,'30%');
 assert.equal(p.run('decoration.phase'),'waiting');
 p.tick(10000);assert.equal(p.run('decoration.phase'),'waiting');
 p.run('decoration.finish(30)');
 for(const [time,available] of [[11000,66],[12000,66.2],[13000,66.4]]){
   p.tick(time);p.run(`memory(${snapshot(available)})`);p.tick(time);
 }
 assert.equal(p.run('decoration.phase'),'returning');
 assert.ok(Math.abs(p.run('decoration.target')-33.6)<1e-9);
 p.tick(13600);const before=p.run('decoration.value');
 p.run(`memory(${snapshot(70)})`);
 assert.equal(p.run('decoration.value'),before);
 assert.equal(p.run('decoration.target'),30);
 p.tick(14200);assert.equal(p.run('decoration.phase'),null);
 assert.equal(p.elements['actual-percent'].textContent,'30%');
});
test('failed report never supplies successful rebound signal',async()=>{
 const p=page();p.elements['decoration-enabled'].checked=true;
 await p.run(`token='session';supportedMethods=['working-set'];el('method').value='working-set';
  api=async path=>path==='/api/action'?{id:'one'}:{kind:'cleanup',state:'finished',report:{completion:'failed',errors:[{message:'no admission'}],samples:[],action:{call_outcome:'not_started'}},presentation:{title:'失败',note:'未派发',context:'记录'}};
  memory(${snapshot(60)});run({kind:'cleanup',mechanism:'working-set'});`);
 assert.equal(p.run('decoration.phase'),null);
 assert.equal(p.elements['recent-title'].textContent,'失败');
});

test('known limited sweep ends normally while keeping raw failures in details',async()=>{
 const p=page();p.elements['decoration-enabled'].checked=true;
 await p.run(`token='session';supportedMethods=['working-set'];el('method').value='working-set';
  var original={completion:'partial',errors:[{message:'维护或恢复存在错误，见动作事实'}],samples:[],
    action:{call_outcome:'failure',status:5,process_sweep:{successful_count:676,failed_count:7,skipped_count:15}}};
  var requests=0;
  api=async path=>path==='/api/action'?(requests++,{id:'one'}):{kind:'cleanup',state:'finished',report:original,
    presentation:{limited:true,title:'已整理 · 可用内存增加0.28 GiB',note:'保留7个未完成目标',context:'真实记录'}};
  memory(${snapshot(60)});run({kind:'cleanup',mechanism:'working-set'});`);
 assert.equal(p.elements.error.hidden,true);
 assert.match(p.elements['job-status'].textContent,/整理已结束/);
 assert.match(p.elements['result-summary'].textContent,/已整理676个进程/);
 assert.match(p.elements['result-summary'].textContent,/7个未完成/);
 assert.match(p.elements.report.textContent,/维护或恢复存在错误/);
 assert.match(p.elements.report.textContent,/"status": 5/);
 assert.equal(p.run('requests'),1);
 assert.equal(p.elements['global-clean'].disabled,false);
 for(const [time,available] of [[1000,70],[2000,70.1],[3000,70.2]]){
   p.tick(time);p.run(`memory(${snapshot(available)})`);p.tick(time);
 }
 assert.equal(p.run('decoration.phase'),'returning');
 p.tick(4200);assert.equal(p.run('decoration.phase'),null);
});

test('unknown result keeps the error and never receives limited completion treatment',async()=>{
 const p=page();p.elements['decoration-enabled'].checked=true;
 await p.run(`token='session';supportedMethods=['working-set'];el('method').value='working-set';
  api=async path=>path==='/api/action'?{id:'one'}:{kind:'cleanup',state:'finished',
    report:{completion:'unknown',errors:[{message:'原生结果未知'}],samples:[],action:{call_outcome:'unknown'}},
    presentation:{limited:false,title:'未知',note:'详见原报告',context:'记录'}};
  memory(${snapshot(60)});run({kind:'cleanup',mechanism:'working-set'});`);
 assert.equal(p.elements.error.hidden,false);
 assert.equal(p.elements.error.textContent,'原生结果未知');
 assert.equal(p.run('decoration.phase'),null);
});

test('early result is immediate while post-return decline holds the circle at zero',async()=>{
 const p=page();p.elements['decoration-enabled'].checked=true;
 await p.run(`token='session';supportedMethods=['working-set'];el('method').value='working-set';
  api=async path=>path==='/api/action'?{id:'one'}:{kind:'cleanup',state:'finished',report:{completion:'success',errors:[],samples:[],action:{call_outcome:'success'}},presentation:{title:'已完成',note:'原报告',context:'记录'}};
  memory(${snapshot(60)});run({kind:'cleanup',mechanism:'working-set'});`);
 assert.equal(p.elements['recent-title'].textContent,'已完成');
 assert.equal(p.run('decoration.phase'),'emptying');
 p.tick(1500);assert.equal(p.elements.percent.textContent,'20%');
 p.run(`memory(${snapshot(70)})`);p.tick(3000);
 assert.equal(p.elements.percent.textContent,'0%');
 assert.equal(p.run('decoration.phase'),'observing');
 for(const [time,available] of [[3500,74],[4500,76],[5500,76.2]]){
   p.tick(time);p.run(`memory(${snapshot(available)})`);p.tick(time);
   assert.equal(p.run('decoration.phase'),'observing');
   assert.equal(p.elements.percent.textContent,'0%');
 }
 p.tick(6500);p.run(`memory(${snapshot(76.3)})`);p.tick(6500);
 assert.equal(p.run('decoration.phase'),'returning');
 assert.ok(Math.abs(p.run('decoration.target')-23.7)<1e-9);
 p.tick(7700);assert.equal(p.elements.percent.textContent,'24%');
 assert.equal(p.run('decoration.phase'),null);
});

test('continuous changes have a bounded observation rather than endless zero',()=>{
 const p=page();p.run(`memory(${snapshot(60)});decoration.start(40);decoration.finish(40)`);
 for(let t=1000;t<=8000;t+=1000){p.tick(t);p.run(`memory(${snapshot(30+t/1000*3)})`);p.tick(t);}
 assert.equal(p.run('decoration.phase'),'returning');
 assert.equal(p.run('decoration.settleReason'),'time_limit');
 p.tick(9200);assert.equal(p.run('decoration.phase'),null);
 assert.equal(p.elements.percent.textContent,'46%');
});

test('missing new readings exits the process display without waiting indefinitely',()=>{
 const p=page();p.run(`memory(${snapshot(60)});decoration.start(40);decoration.finish(40)`);
 p.tick(3000);assert.equal(p.run('decoration.phase'),'observing');
 p.tick(8000);assert.equal(p.run('decoration.phase'),null);
 assert.equal(p.run('decoration.settleReason'),'no_new_reading');
 assert.equal(p.elements.percent.textContent,'40%');
});

test('duplicate sample keys do not establish a quiet window',()=>{
 const p=page();p.run(`memory(${snapshot(60)});decoration.start(40);decoration.finish(40)`);
 for(let t=1000;t<=3000;t+=1000){p.tick(t);p.run('decoration.track(30,"same")');p.tick(t);}
 assert.equal(p.run('decoration.phase'),'observing');
 assert.equal(p.run('decoration.readings.length'),1);
 p.tick(4000);p.run('decoration.track(30,"new-1")');p.tick(4000);
 p.tick(5000);p.run('decoration.track(30,"new-2")');p.tick(5000);
 assert.equal(p.run('decoration.phase'),'returning');
});

test('older response cannot replace a newer physical sample',()=>{
 const p=page();
 const report=(available,stamp)=>`({samples:[{timestamp:'2026-11-02T00:00:${stamp}+00:00',metrics:{physical:{total_bytes:100,available_bytes:${available}}}}]})`;
 p.run(`memory(${report(40,'10')})`);
 p.run(`memory(${report(80,'09')})`);p.tick(400);
 assert.equal(p.elements['actual-percent'].textContent,'60%');
 p.run(`memory(${report(75,'11')})`);p.tick(800);
 assert.equal(p.elements['actual-percent'].textContent,'25%');
});
