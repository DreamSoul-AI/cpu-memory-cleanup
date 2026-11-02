const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
function display(reduced=false){
  let now=0,id=0;const frames=new Map(),painted=[];
  const context=vm.createContext({performance:{now:()=>now},requestAnimationFrame(fn){frames.set(++id,fn);return id;},cancelAnimationFrame(id){frames.delete(id);}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../../src/interaction/web_assets/memory-display.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,"../../../src/interaction/web_assets/cleanup-flow.js"),"utf8"),context);
  context.render=(value,phase)=>painted.push({value,phase});context.reduced=()=>reduced;
  const model=vm.runInContext('new CleanupFlow(render,async()=>20,reduced)',context);
  return {model,painted,frames,tick(time){now=time;for(const [key,fn] of [...frames]){frames.delete(key);fn(time);}}};
}
test('observations retarget one continuous position without starting a second frame loop',()=>{
  const p=display();p.model.update(60);p.model.update(40);p.tick(100);
  const current=p.model.value;assert.ok(current>40&&current<60);
  p.model.update(20);p.model.update(10);assert.equal(p.model.value,current);assert.equal(p.frames.size,1);
  p.tick(116);assert.ok(p.model.value<current&&p.model.value>10);
  p.tick(1200);assert.equal(p.model.value,10);assert.equal(p.frames.size,0);
});
test('rebound only lowers its cycle target and uses target/15 per 20ms step',async()=>{
  const p=display();p.model.update(40);p.model.refresh=async()=>35;p.model.start();p.tick(750);
  for(let i=0;i<4;i++)await Promise.resolve();
  p.tick(770);assert.equal(p.model.value,35/15);
  const position=p.model.value;p.model.update(26,{timestamp:'new',source:'live'});
  assert.equal(p.model.value,position);assert.equal(p.model.step,26/15);
  p.model.update(38);assert.equal(p.model.target,26);assert.equal(p.model.actual,38);
  p.tick(790);assert.ok(Math.abs(p.model.value-position-26/15)<1e-9);
  for(let time=810;time<=1150;time+=20)p.tick(time);
  assert.equal(p.model.phase,null);assert.ok(p.model.value>=26);
});
test('lower target late in rebound does not accelerate to an old deadline',async()=>{
  const p=display();p.model.update(40);p.model.refresh=async()=>35;p.model.start();p.tick(750);
  for(let i=0;i<4;i++)await Promise.resolve();
  for(let time=770;time<=990;time+=20)p.tick(time);
  const position=p.model.value;p.model.update(26);
  assert.equal(p.model.value,position);assert.equal(p.model.step,26/15);
  p.tick(1010);assert.equal(p.model.value,26);assert.equal(p.model.phase,null);
});
test('new visual cycle does not inherit the previous minimum',async()=>{
  const p=display();p.model.update(40);p.model.refresh=async()=>20;p.model.start();p.tick(750);
  for(let i=0;i<4;i++)await Promise.resolve();
  p.model.update(60);p.model.begin();p.model.start();assert.equal(p.model.target,60);
});
test('business hold preserves the displayed origin while actual readings continue',()=>{
  const p=display();p.model.update(40);p.model.begin();p.model.update(26);p.tick(5000);
  assert.equal(p.model.value,40);assert.equal(p.model.actual,26);assert.equal(p.frames.size,0);
  p.model.start();p.tick(5375);assert.equal(p.model.value,20);
});
test('hidden display stops frames and resumes without a fabricated long frame gap',()=>{
  const p=display();p.model.update(20);p.model.update(80);p.tick(20);const previous=p.model.value;
  p.model.suspend();p.model.update(70);assert.equal(p.frames.size,0);p.tick(10000);assert.equal(p.model.value,previous);
  p.model.resume();p.tick(10016);assert.ok(p.model.value>previous&&p.model.value<70);assert.ok(p.model.stats().max_gap_ms<100);
});
test('reduced motion uses observed values without a visual workflow',()=>{
  const p=display(true);p.model.update(20);p.model.update(70);p.model.start();
  assert.equal(p.model.value,70);assert.equal(p.model.phase,null);assert.equal(p.frames.size,0);
});
test('background readings cannot move the zero display while its one readonly refresh is pending',async()=>{
  const p=display();let release;let requests=0;
  p.model.refresh=()=>{requests++;return new Promise(resolve=>release=resolve);};
  p.model.update(40);p.model.start();p.tick(750);await Promise.resolve();
  p.model.update(65);p.tick(900);
  assert.equal(p.model.value,0);assert.equal(p.model.phase,'refreshing');assert.equal(p.frames.size,0);assert.equal(requests,1);
  release(20);for(let i=0;i<4;i++)await Promise.resolve();
  assert.equal(p.model.phase,'returning');assert.equal(p.model.target,20);
});
