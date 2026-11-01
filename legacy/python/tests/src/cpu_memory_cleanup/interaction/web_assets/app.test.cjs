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
    AbortSignal,setTimeout(){return 0;},clearTimeout(){},fetch:()=>new Promise(()=>{})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../../../../../src/cpu_memory_cleanup/interaction/web_assets/cleanup-effect.js"),"utf8"),context);
  vm.runInContext(source,context);
  return {elements,run:s=>vm.runInContext(s,context),tick(time){clock=time;for(const id of [...frames.keys()]){const fn=frames.get(id);frames.delete(id);if(fn)fn(time);}},frames};
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
    else assert.equal(p.elements['gauge-label'].textContent,'整理已结束 · 归零动效');
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


test('descent requests one fresh reading at zero then rebounds immediately',async()=>{
 const p=page();p.run(`memory(${snapshot(60)});var refreshCount=0;decoration.refresh=async()=>{refreshCount++;return 25};decoration.start(40)`);
 p.tick(375);assert.equal(p.elements.percent.textContent,'20%');
 assert.equal(p.run('refreshCount'),0);
 p.tick(750);assert.equal(p.elements.percent.textContent,'0%');
 await p.run('Promise.resolve()');await p.run('Promise.resolve()');
 assert.equal(p.run('refreshCount'),1);assert.equal(p.run('decoration.phase'),'returning');
 p.run(`memory(${snapshot(75)})`);p.tick(900);
 assert.equal(p.elements.percent.textContent,'13%');
 p.tick(1050);assert.equal(p.run('decoration.phase'),null);assert.equal(p.elements.percent.textContent,'25%');
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
 assert.equal(p.run('decoration.phase'),'emptying');
 p.run('decoration.stop()');
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

test('business finishes before descent and zero triggers only a readonly refresh',async()=>{
 const p=page();p.elements['decoration-enabled'].checked=true;
 p.run(`token='session';readingSupported=true;supportedMethods=['working-set'];el('method').value='working-set';
  var complete,requests=[];var pending=new Promise(resolve=>complete=resolve);
  api=async (path,body,options)=>{requests.push(path);if(path==='/api/action')return {id:'one'};if(path==='/api/display')return {completion:'success',...${snapshot(75)}};return pending;};
  memory(${snapshot(60)});var running=run({kind:'cleanup',mechanism:'working-set'});`);
 await p.run('Promise.resolve()');p.tick(5000);
 assert.equal(p.run('decoration.phase'),null);assert.equal(p.elements.percent.textContent,'40%');
 assert.equal(p.elements['global-clean'].disabled,true);
 p.run(`complete({kind:'cleanup',state:'finished',report:{completion:'success',errors:[],samples:[],action:{call_outcome:'success'}},presentation:{title:'真实结果',note:'原报告',context:'记录'}})`);
 await p.run('running');
 assert.equal(p.elements['recent-title'].textContent,'真实结果');assert.equal(p.elements['global-clean'].disabled,false);
 assert.equal(p.run('decoration.phase'),'emptying');
 p.tick(5375);assert.equal(p.elements.percent.textContent,'20%');
 assert.equal(p.run('requests.filter(path=>path==="/api/display").length'),0);
 p.tick(5750);await p.run('Promise.resolve()');await p.run('Promise.resolve()');await p.run('Promise.resolve()');
 assert.equal(p.run('decoration.phase'),'returning');
 assert.equal(p.run('requests.filter(path=>path==="/api/display").length'),1);
 assert.equal(p.run('requests.filter(path=>path==="/api/action").length'),1);
 p.tick(6050);assert.equal(p.elements.percent.textContent,'25%');assert.equal(p.run('decoration.phase'),null);
 assert.equal(p.elements['recent-title'].textContent,'真实结果');
});

test('changing live readings do not add a stability wait before rebound',async()=>{
 const p=page();p.run(`memory(${snapshot(60)});decoration.refresh=async()=>20;decoration.start(40)`);
 p.tick(250);p.run(`memory(${snapshot(70)})`);
 p.tick(500);p.run(`memory(${snapshot(75)})`);
 p.tick(750);await p.run('Promise.resolve()');await p.run('Promise.resolve()');
 assert.equal(p.run('decoration.phase'),'returning');assert.equal(p.run('decoration.target'),20);
 p.tick(1050);assert.equal(p.run('decoration.phase'),null);
});

test('refresh failure preserves completed report and last actual value',async()=>{
 const p=page();p.run(`token='session';readingSupported=true;memory(${snapshot(60)});
 el('recent-title').textContent='已整理';api=async()=>{throw new Error('timeout')};decoration.start(40)`);
 p.tick(750);for(let i=0;i<5;i++)await p.run('Promise.resolve()');
 assert.equal(p.run('decoration.phase'),null);assert.equal(p.elements.percent.textContent,'40%');
 assert.equal(p.elements['recent-title'].textContent,'已整理');assert.match(p.elements['live-status'].textContent,/timeout/);
});

test('late refresh from an old cycle cannot finish a new animation',async()=>{
 const p=page();p.run(`memory(${snapshot(60)});var release;decoration.refresh=()=>new Promise(resolve=>release=resolve);decoration.start(40)`);
 p.tick(750);await p.run('Promise.resolve()');
 p.run('decoration.start(30);release(10)');for(let i=0;i<3;i++)await p.run('Promise.resolve()');
 assert.equal(p.run('decoration.phase'),'emptying');assert.equal(p.run('decoration.origin'),30);
 p.tick(1125);assert.equal(p.elements.percent.textContent,'15%');
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


test('reduced motion skips visual workflow and extra display request',async()=>{
 const p=page(true);p.elements['decoration-enabled'].checked=true;
 await p.run(`token='session';readingSupported=true;supportedMethods=['working-set'];el('method').value='working-set';var paths=[];
 api=async path=>{paths.push(path);return path==='/api/action'?{id:'one'}:{kind:'cleanup',state:'finished',report:{completion:'success',errors:[],samples:[],action:{call_outcome:'success'}},presentation:{title:'已完成',note:'结果',context:'记录'}};};
 memory(${snapshot(60)});run({kind:'cleanup',mechanism:'working-set'});`);
 p.tick(5000);assert.equal(p.run('decoration.phase'),null);
 assert.equal(p.run('paths.includes("/api/display")'),false);
 assert.equal(p.elements['recent-title'].textContent,'已完成');
});
