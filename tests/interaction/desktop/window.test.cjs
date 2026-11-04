// Opt-in Windows WebView2 e2e | p1 | physical | C2 | categorical + numeric.
// Fixed 128 MiB self-owned load. Never selects another application or enables rules.
// Default tests do not enable this journey; a new output directory prevents replay.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn,execFileSync} = require('node:child_process');
const readline = require('node:readline');
const configured=process.platform==='win32'&&process.env.MEMORY_WINDOW_EXE&&process.env.MEMORY_WINDOW_SHORTCUT&&process.env.MEMORY_WINDOW_DRIVER&&process.env.MEMORY_WINDOW_OUTPUT;

test('installed native window opens, handles its owned task, stops and reopens',{skip:!configured,timeout:180000},async t=>{
  const out=path.resolve(process.env.MEMORY_WINDOW_OUTPUT);
  fs.mkdirSync(out,{recursive:false});
  const exe=path.resolve(process.env.MEMORY_WINDOW_EXE), shortcut=path.resolve(process.env.MEMORY_WINDOW_SHORTCUT);
  const cli=path.join(path.dirname(exe),'cpu-memory-cleanup.exe');
  const {chromium}=require(path.resolve(process.env.MEMORY_WINDOW_DRIVER));
  const url='http://127.0.0.1:14801/';
  const endpoint='http://127.0.0.1:18780';
  const allowOwned=process.env.MEMORY_WINDOW_OWNED_ACTION==='1';
  let browser,page,child,session,owned;
  const events=[];
  const save=(name,value)=>fs.writeFileSync(path.join(out,name+'.json'),JSON.stringify(value,null,2));
  const delay=ms=>new Promise(r=>setTimeout(r,ms));
  async function until(fn,timeout=15000){const start=Date.now();for(;;){try{const value=await fn();if(value)return value;}catch(_){}if(Date.now()-start>timeout)throw new Error('Timed out waiting for owned window state');await delay(100);}}
  function pids(){return execFileSync('tasklist.exe',['/FI','IMAGENAME eq memory-cleanup-window.exe','/FO','CSV','/NH']).toString().split(/\r?\n/).map(s=>/^"memory-cleanup-window\.exe","([0-9]+)"/i.exec(s)).filter(Boolean).map(m=>Number(m[1]));}
  function call(name,args){const text=execFileSync(cli,args,{encoding:'utf8',windowsHide:true});const value=JSON.parse(text);save(name,value);return value;}
  async function launch(){await new Promise((resolve,reject)=>{const p=spawn(path.join(process.env.SystemRoot,'System32','rundll32.exe'),['url.dll,FileProtocolHandler',shortcut],{windowsHide:true,stdio:'ignore',env:{...process.env,WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS:'--remote-debugging-port=18780 --remote-debugging-address=127.0.0.1'}});p.on('error',reject);p.on('exit',code=>code===0?resolve():reject(new Error('Shortcut launcher exit '+code)));});}
  async function connect(){
    session=await until(async()=>{const r=await fetch(url+'api/session');return r.ok?r.json():null;});
    assert.equal(session.build_id,catalog.build_id);
    await until(async()=>{const r=await fetch(endpoint+'/json/version');return r.ok;});
    browser=await chromium.connectOverCDP(endpoint);
    page=await until(()=>browser.contexts().flatMap(c=>c.pages()).find(p=>p.url()===url));
    await page.waitForFunction(()=>document.getElementById('actual-percent').textContent.endsWith('%'));
    assert.match(await page.locator('#capacity').innerText(),/GiB/);
    assert.equal(session.preview,false);
    assert.equal(session.capabilities.mechanisms[0],'working-set');
    const state=await (await fetch(url+'api/state',{headers:{'X-Session-Token':session.token}})).json();
    assert.equal(state.rule.armed,false);
  }
  async function stop(){
    await page.getByRole('button',{name:'停止服务',exact:true}).click();
    await page.getByRole('button',{name:'确认本次操作',exact:true}).click();
    await until(()=>pids().length===0);
    try{await browser.close();}catch(_){}
    browser=null;page=null;
  }
  async function stage(name,fn){let error;await t.test(name,async()=>{const started=Date.now();try{await fn();events.push({name,status:'passed',elapsed_ms:Date.now()-started});}catch(e){error=e;events.push({name,status:'failed',elapsed_ms:Date.now()-started,error:String(e)});throw e;}finally{save('stages',events);}});if(error)throw error;}
  assert.equal(pids().length,0,'Do not replace or attach to a user-owned native window');
  const catalog=call('catalog',['--tool-info']);
  try{
    await stage('real installed shortcut starts one native window',async()=>{
      await launch();await connect();assert.equal(pids().length,1);
      save('initial',{build_id:session.build_id,actual:await page.locator('#actual-percent').innerText(),capacity:await page.locator('#capacity').innerText(),pids:pids()});
      await page.screenshot({path:path.join(out,'initial.png')});
    });
    await stage('repeat shortcut keeps the existing native process and service',async()=>{
      const before=pids();const token=session.token;
      await launch();await delay(500);await until(()=>JSON.stringify(pids())===JSON.stringify(before));
      assert.equal(browser.contexts().flatMap(c=>c.pages()).filter(p=>p.url()===url).length,1);
      const current=await (await fetch(url+'api/session')).json();assert.equal(current.token,token);
      save('repeat',{pids:pids(),one_view:true,same_session:true});
    });
    if(allowOwned){
      await stage('native controls dispatch one exact owned process and preserve its data',async()=>{
        const loadCode=`const crypto=require('node:crypto');const b=Buffer.alloc(128*1024*1024,90);const hash=()=>crypto.createHash('sha256').update(b).digest('hex');const original=hash();console.log(JSON.stringify({pid:process.pid,bytes:b.length,sha256:original}));require('node:readline').createInterface({input:process.stdin}).on('line',s=>{if(s==='exit')process.exit(0);else console.log(JSON.stringify({verified:hash()===original}));});`;
        child=spawn(process.execPath,['-e',loadCode],{windowsHide:true,stdio:['pipe','pipe','pipe']});
        const lines=[],waiters=[];readline.createInterface({input:child.stdout}).on('line',s=>waiters.length?waiters.shift()(s):lines.push(s));
        const next=()=>lines.length?Promise.resolve(lines.shift()):new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(new Error('Owned load did not answer')),10000);waiters.push(s=>{clearTimeout(timer);resolve(s);});});
        owned=JSON.parse(await next());assert.equal(owned.bytes,134217728);save('owned-load',owned);
        const targets=call('owned-before',['--tool','memory_targets']);const target=targets.samples[0].processes.find(p=>p.pid===owned.pid);assert.ok(target?.created_at);
        await page.locator('#more > summary').click();
        await page.getByRole('button',{name:'读取进程列表',exact:true}).click();
        await page.getByRole('searchbox',{name:'查找',exact:true}).fill(String(owned.pid));
        await page.getByRole('checkbox',{name:`选择 ${target.name} PID ${owned.pid}`,exact:true}).check();
        await page.getByRole('button',{name:'整理选中对象',exact:true}).click();
        const scope=await page.locator('#confirmation-text').innerText();
        assert.ok(scope.includes(`PID ${owned.pid}`));assert.match(scope,/固定 1 个身份/);assert.match(scope,/0 项命中保护/);
        save('maintenance-intent',{authorization:'standing approval: only one owned 128 MiB process, no other apps or rules',target,scope,maximum_dispatches:1});
        await page.getByRole('button',{name:'确认本次操作',exact:true}).click();
        await page.getByRole('button',{name:'离线回看本次记录',exact:true}).waitFor({state:'visible'});
        await until(async()=>!(await page.getByRole('button',{name:'离线回看本次记录',exact:true}).isDisabled()));
        await page.locator('#report-details > summary').click();
        await page.waitForFunction(()=>{try{return Array.isArray(JSON.parse(document.getElementById('report').textContent).action?.process_sweep?.items);}catch(_){return false;}});
        const report=JSON.parse(await page.locator('#report').innerText());save('owned-cleanup',report);
        assert.equal(report.completion,'success');assert.equal(report.metadata.build_id,catalog.build_id);
        const sweep=report.action.process_sweep;assert.equal(sweep.attempted_count,1);assert.equal(sweep.items.length,1);assert.equal(sweep.items[0].pid,owned.pid);assert.equal(sweep.items[0].created_at,target.created_at);
        assert.ok(sweep.items[0].working_set_after_bytes<sweep.items[0].working_set_before_bytes);
        child.stdin.write('verify\n');const content=JSON.parse(await next());assert.equal(content.verified,true);save('owned-content',content);
        await page.locator('#report-details > summary').click();
        await page.locator('#more > summary').click();
        await page.waitForFunction(()=>{try {const m=JSON.parse(document.getElementById('gauge').getAttribute('data-display-metrics'));return !m.motion.phase;} catch(_){return false;}});
        save('display-timeline',await page.locator('#gauge').evaluate(el=>({flow:JSON.parse(el.getAttribute('data-flow')),metrics:JSON.parse(el.getAttribute('data-display-metrics'))})));
        assert.equal(await page.locator('#gauge-label').innerText(),'物理内存占用');
        await page.screenshot({path:path.join(out,'result.png')});
        const replay=call('cli-review',['--tool','memory_result','--run-dir',report.output_dir]);assert.equal(replay.metadata.source_run_id,report.run_id);
        save('maintenance-summary',{run_id:report.run_id,pid:owned.pid,created_at:target.created_at,build_id:catalog.build_id,native:sweep.items[0],verified:true,rule_armed:false});
      });
    }else{await t.test('owned maintenance requires explicit opt-in',{skip:true},()=>{});}
    await stage('native stop exits, shortcut reopens without automatic maintenance',async()=>{
      const previous=session.token;await stop();await launch();await connect();assert.notEqual(session.token,previous);
      assert.equal(await page.locator('#recent-title').innerText(),'尚未整理');
      save('reopened',{build_id:session.build_id,actual:await page.locator('#actual-percent').innerText(),new_session:true,no_automatic_action:true});
      await stop();
    });
  }finally{
    if(child && child.exitCode===null){child.stdin.write('exit\n');await new Promise(resolve=>child.once('exit',resolve));}
    if(session){try{await fetch(url+'api/shutdown',{method:'POST',headers:{'X-Session-Token':session.token,'Content-Type':'application/json'},body:'{"confirmed":true}'});}catch(_){} }
    if(browser){try{await browser.close();}catch(_){} }
    save('finished',{at:new Date().toISOString(),remaining_native_pids:pids(),stages:events});
  }
});
