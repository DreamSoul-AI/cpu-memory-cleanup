const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../../../../src/cpu_memory_cleanup/interaction/web_assets/app.js'), 'utf8');

function page(reduced = false) {
  const elements = {}, frames = new Map(); let next = 0;
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
    window:{addEventListener(){}}, matchMedia:()=>({matches:reduced}),performance:{now:()=>0},
    requestAnimationFrame(fn){frames.set(++next,fn);return next;},cancelAnimationFrame(id){frames.delete(id);},
    setTimeout(){return 0;},clearTimeout(){},fetch:()=>new Promise(()=>{})});
  vm.runInContext(source,context);
  return {elements,run:s=>vm.runInContext(s,context),tick(time){const jobs=[...frames.values()];frames.clear();jobs.forEach(fn=>fn(time));},frames};
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
test('working indicator belongs to actual ring and disables repeated main clicks',()=>{
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

test('live occupancy continues updating during cleanup and never becomes an effect value',()=>{
  const p=page();p.run('busy=true;cleaning=true');
  p.run(`memory(${snapshot(60)})`);
  p.run(`memory(${snapshot(70)})`);p.tick(400);
  assert.equal(p.elements.percent.textContent,'30%');
  assert.equal(p.elements.gauge.style['--usage'],'108deg');
  assert.equal(p.elements.meter.attrs['aria-valuenow'],'30');
  p.run(`memory(${snapshot(50)})`);p.tick(400);
  assert.equal(p.elements.percent.textContent,'50%');
  assert.equal(p.elements.gauge.style['--usage'],'180deg');
  assert.equal(p.elements.meter.attrs['aria-valuenow'],'50');
});
