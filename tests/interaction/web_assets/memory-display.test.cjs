const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
test('position driver only paints requested values and interpolates without requests or clocks',()=>{
  const context=vm.createContext({});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../../../src/interaction/web_assets/memory-display.js'),'utf8'),context);
  const Driver=vm.runInContext('MemoryDisplay',context), paints=[];
  const driver=new Driver((value,phase)=>paints.push({value,phase}));
  driver.paint(26,'returning');assert.deepEqual(paints,[{value:26,phase:'returning'}]);
  assert.equal(driver.interpolate(0,40,.5),20);
  assert.ok(driver.follow(35,26,16)<35);
  assert.equal(paints.length,1);
});
