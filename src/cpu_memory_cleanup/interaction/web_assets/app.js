"use strict";
const el = (id) => document.getElementById(id);
let cleaning = false;
let liveEnabled = true, displayTimer = null, displayInFlight = false, animation = null, shownMemory = null;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let token = "", busy = false, currentJob = null, lastSaved = null, pendingAction = null;
let rows = [], selected = new Set(), descending = true, stopped = false;
let settings = {schema_version:1,protections:[],descending:true,resident:false,geometry:""};
let supportedMethods = [], readingSupported = false, ruleTimer = null, seenRuleJob = null;
const normalizePath = (path) => path.replaceAll("\\", "/").toLowerCase();
function protection(row) {
  for (const rule of settings.protections) {
    if (rule.image_path) {
      if (!row.image_path) return "路径未知，按保护跳过";
      if (normalizePath(row.image_path) === normalizePath(rule.image_path)) return "受保护";
    } else if (row.pid === rule.pid && row.created_at === rule.created_at) return "实例受保护";
  }
  return "";
}
const key = (row) => `${row.pid}:${row.created_at}`;
const gib = (value) => value == null ? "未知" : `${(value / 1024 ** 3).toFixed(2)} GiB`;
const labels = {success:"完成", partial:"结果不完整", failed:"失败", cancelled:"已取消", skipped:"已跳过", not_started:"未执行"};
function error(message) { el("error").textContent = message; el("error").hidden = !message; }
function controls() {
  for (const id of ["read-targets","global-clean","shutdown"]) el(id).disabled = busy || stopped || !token;
  el("read-status").disabled = stopped || !token || !readingSupported;
  el("read-status").textContent = liveEnabled ? "暂停实时显示" : "恢复实时显示";
  el("global-clean").textContent = busy && cleaning ? "正在整理…" : el("method").value === "standby" ? "高级：整理系统 Standby" : "✦ 一键整理工作集";
  el("global-clean").setAttribute("aria-busy",String(busy && cleaning));
  el("gauge").classList.toggle("working",busy && cleaning);
  el("read-targets").disabled ||= !readingSupported;
  el("global-clean").disabled ||= !supportedMethods.includes(el("method").value);
  for(const id of ["selected-clean","protect-selected","arm-rule"]) el(id).disabled = busy || stopped || !selected.size || !token;
  el("unprotected-clean").disabled = busy || stopped || !rows.length || !token;
  el("cancel").disabled = !busy || stopped;
  el("replay").disabled = busy || stopped || !lastSaved;
}
async function api(path, body) {
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST",
    headers: {"X-Session-Token":token, ...(body === undefined ? {} : {"Content-Type":"application/json"})},
    ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function renderRows() {
  const query = el("search").value.trim().toLowerCase();
  const visible = rows.filter(row=>`${row.name} ${row.pid} ${row.image_path||""}`.toLowerCase().includes(query));
  visible.sort((a,b)=>(descending ? -1 : 1)*((a.working_set_bytes||0)-(b.working_set_bytes||0)));
  el("targets").replaceChildren();
  let groups = new Map();
  for (const row of visible) {
    const group = row.image_path ? normalizePath(row.image_path) : key(row);
    if(!groups.has(group)) groups.set(group,[]);
    groups.get(group).push(row);
  }
  const ordered = el("grouped").checked ? [...groups.values()].flat() : visible;
  const added = new Set();
  for (const row of ordered) {
    const groupKey = row.image_path ? normalizePath(row.image_path) : key(row);
    if(el("grouped").checked && !added.has(groupKey)) {
      added.add(groupKey);
      const members=groups.get(groupKey), tr=document.createElement("tr"), cell=document.createElement("td"), box=document.createElement("input"), title=document.createElement("td");
      tr.className="group-row";box.type="checkbox";box.checked=members.every(r=>selected.has(key(r)));box.disabled=busy;
      box.setAttribute("aria-label",`选择程序文件组 ${row.image_path||row.name}`);
      box.addEventListener("change",()=>{for(const member of members)box.checked?selected.add(key(member)):selected.delete(key(member));renderRows();controls();});
      cell.append(box);title.colSpan=3;title.textContent=`${row.name} · 同一程序文件 ${members.length} 项`;tr.append(cell,title);el("targets").append(tr);
    }
    const tr=document.createElement("tr"), td=document.createElement("td"), checkbox=document.createElement("input");
    checkbox.type="checkbox"; checkbox.checked=selected.has(key(row)); checkbox.disabled=busy;
    checkbox.setAttribute("aria-label",`选择 ${row.name} PID ${row.pid}`);
    checkbox.addEventListener("change",()=>{checkbox.checked?selected.add(key(row)):selected.delete(key(row)); controls(); count();});
    td.append(checkbox);tr.append(td);
    const name=document.createElement("td"), title=document.createElement("strong"), path=document.createElement("small");
    title.textContent=row.name;path.textContent=(row.image_path||"路径未知")+(protection(row)?` · ${protection(row)}`:"");name.append(title,path);tr.append(name);
    for(const text of [String(row.pid),gib(row.working_set_bytes)]){const cell=document.createElement("td");cell.textContent=text;tr.append(cell);}
    el("targets").append(tr);
  }
  if(!visible.length){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=4;td.className="empty";td.textContent=rows.length?"没有匹配的进程。":"按需读取列表，然后选择进程。";tr.append(td);el("targets").append(tr);}
  count();
}
function count(){el("selection-count").textContent=`${rows.length} 个目标 · 已选 ${selected.size} 个`;}
function paintMemory(value){
  shownMemory=value;
  const {total,available,commitUsed,commitLimit}=value;
  const valid=total>0 && available!=null && available>=0 && available<=total;
  const pct=valid?(total-available)/total*100:null;
  el("percent").textContent=pct==null?"未知":`${Math.round(pct)}%`;
  el("fill").style.width=`${pct||0}%`;
  el("gauge").style.setProperty("--usage",`${pct==null?0:pct*3.6}deg`);
  if(pct!=null)el("meter").setAttribute("aria-valuenow",String(Math.round(pct)));else el("meter").removeAttribute("aria-valuenow");
  el("capacity").textContent=`${gib(available)} / ${gib(total)}`;
  el("commit").textContent=`${gib(commitUsed)} / ${gib(commitLimit)}`;
}
function memory(report){
  const sample=report.samples?.at(-1);if(!sample)return;
  const physical=sample.metrics.physical,commit=sample.metrics.commit;if(!physical)return;
  const target={total:physical.total_bytes,available:physical.available_bytes,commitUsed:commit?.used_bytes,commitLimit:commit?.limit_bytes};
  if(animation!==null)cancelAnimationFrame(animation);
  animation=null;
  if(!shownMemory||reducedMotion.matches||target.available==null){paintMemory(target);return;}
  const before={...shownMemory},started=performance.now();
  function frame(now){
    const progress=Math.min(1,Math.max(0,(now-started)/400));
    const weight=progress*progress*(3-2*progress);
    const value={};for(const name of Object.keys(target))value[name]=before[name]!=null&&target[name]!=null?before[name]+(target[name]-before[name])*weight:target[name];
    paintMemory(progress===1?target:value);
    animation=progress<1?requestAnimationFrame(frame):null;
  }
  animation=requestAnimationFrame(frame);
}
async function refreshDisplay(){
  clearTimeout(displayTimer);
  if(!liveEnabled||document.hidden||stopped||!token||!readingSupported||displayInFlight)return;
  displayInFlight=true;
  try{
    const report=await api("/api/display",{});
    if(!liveEnabled||document.hidden||stopped)return;
    if(report.completion!=="success")throw new Error(report.errors?.map(e=>e.message).join("；")||"内存读数不完整");
    memory(report);
    const stamp=report.samples.at(-1)?.timestamp;
    el("live-status").textContent=`实时显示 · ${stamp?new Date(stamp).toLocaleTimeString():"刚刚更新"} · 平滑过渡`;
  }catch(exc){el("live-status").textContent=`显示暂未更新，保留上次读数 · ${exc.message}`;}
  finally{displayInFlight=false;if(liveEnabled&&!document.hidden&&!stopped)displayTimer=setTimeout(refreshDisplay,1000);}
}
function pauseDisplay(){
  clearTimeout(displayTimer);if(animation!==null)cancelAnimationFrame(animation);animation=null;
}
document.addEventListener("visibilitychange",()=>{if(document.hidden)pauseDisplay();else refreshDisplay();});
window.addEventListener("pagehide",()=>{liveEnabled=false;pauseDisplay();clearTimeout(ruleTimer);});
function present(job){
  const report=job.report;if(job.kind!=="replay")memory(report);
  if(job.kind==="targets"){
    rows=report.samples[0]?.processes||[];const present=new Set(rows.map(key));selected=new Set([...selected].filter(k=>present.has(k)));renderRows();
    if(report.completion!=="success") error("列表可能不完整："+JSON.stringify(report.errors.concat(report.samples[0]?.errors||[])));
    return;
  }
  if(job.kind==="status"){if(report.completion!=="success")error(JSON.stringify(report.errors.concat(report.samples.at(-1)?.errors||[])));return;}
  const feedback=job.presentation;
  if(feedback){el("recent-title").textContent=feedback.title;el("recent-note").textContent=feedback.note;el("recent-context").textContent=feedback.context;}
  el("result-items").replaceChildren();
  const action=report.action||{};
  if(report.errors?.length) error(report.errors.map(item=>item.message||item.type||"未知错误").join("；"));
  const scope=report.metadata?.header?.scope;
  if(scope==="process" && report.metadata?.header?.target?.image_path) el("job-status").title=report.metadata.header.target.image_path;
  const actionText={success:"调用成功",failure:"调用失败",not_started:"未派发",unknown:"调用结果未知"}[action.call_outcome]||"逐项结果见下方";
  el("result-summary").textContent=`${labels[report.completion]||report.completion} · ${job.kind==="batch" || report.mode==="batch-review"?"逐项记录，不累加为系统释放量":actionText}。容量变化不代表提速。`;
  const items=report.metadata?.items||[];
  for(const item of items){const div=document.createElement("div");div.className="result-line";const a=item.analysis||{};div.textContent=`${item.target.name||"目标"} · PID ${item.target.pid} · ${labels[item.status]||item.status} · 工作集 ${gib(a.working_set_before_bytes)} → ${gib(a.working_set_after_bytes)} ${item.reason||""}`;el("result-items").append(div);}
  if(!items.length && report.samples?.length>=2){const div=document.createElement("div");div.className="result-line";div.textContent=`系统可用量：${gib(report.samples[0].metrics.physical?.available_bytes)} → ${gib(report.samples.at(-1).metrics.physical?.available_bytes)}`;el("result-items").append(div);}
  el("report").textContent=JSON.stringify(report,null,2);
  if(report.output_dir && job.kind!=="replay")lastSaved=job.id;
}
async function run(action){
  if(busy||stopped)return;error("");busy=true;cleaning=action.kind==="cleanup"||action.kind==="batch";controls();renderRows();
  el("job-status").textContent="正在提交本次请求…";
  try{
    const accepted=await api("/api/action",action);currentJob=accepted.id;
    el("job-status").textContent="处理中 · 等待 Package 收尾";
    for(;;){const job=await api(`/api/job?id=${encodeURIComponent(currentJob)}`);if(job.state!=="running"){if(job.state==="error")throw new Error(job.error);present(job);el("job-status").textContent=`本次${labels[job.report.completion]||job.report.completion}`;break;}await new Promise(resolve=>setTimeout(resolve,120));}
  }catch(exc){error(exc.message);el("job-status").textContent="本次未完成，请核对结果；不自动重试整理。";}
  finally{busy=false;cleaning=false;currentJob=null;controls();renderRows();}
}
function confirmAction(action,text){pendingAction=action;el("confirmation-text").textContent=text;el("confirmation").hidden=false;el("confirm-yes").focus();}
el("read-status").addEventListener("click",()=>{liveEnabled=!liveEnabled;if(liveEnabled)refreshDisplay();else{pauseDisplay();el("live-status").textContent="实时显示已暂停 · 保留上次读数";}controls();});
el("read-targets").addEventListener("click",()=>run({kind:"targets"}));
el("search").addEventListener("input",renderRows);
el("sort").addEventListener("click",async()=>{descending=!descending;el("sort").textContent=descending?"工作集 ↓":"工作集 ↑";renderRows();try{applyState(await api("/api/settings",{...settings,descending}));}catch(exc){error(exc.message);}});
el("grouped").addEventListener("change",renderRows);
el("method").addEventListener("change",()=>{el("global-clean").textContent=el("method").value==="standby"?"高级：整理系统 Standby":"✦ 一键整理工作集";controls();});
el("global-clean").addEventListener("click",()=>{
  const mechanism=el("method").value;
  if(mechanism==="working-set"){run({kind:"cleanup",mechanism,confirmed:true});return;}
  confirmAction({kind:"cleanup",mechanism,confirmed:true},`本次执行一次系统范围${mechanism==="standby"?" Standby":"工作集"}整理。\n不支持任何保护名单或进程排除，可能增加后续缺页和 I/O。`);
});
function batchConfirmation(targets) {
  const skipped=targets.filter(row=>protection(row));
  confirmAction({kind:"batch",targets:targets.map(key),confirmed:true},`固定 ${targets.length} 个身份，其中 ${skipped.length} 项命中保护将跳过：\n${targets.map(r=>`${r.name} · PID ${r.pid}${protection(r)?" · "+protection(r):""}`).join("\n")}\n只对非保护对象执行工作集整理，不处理列表之外的其他进程。`);
}
el("selected-clean").addEventListener("click",()=>batchConfirmation(rows.filter(r=>selected.has(key(r)))));
el("unprotected-clean").addEventListener("click",()=>batchConfirmation(rows));
el("protect-selected").addEventListener("click",async()=>{
  const protections=settings.protections.map(rule=>({...rule}));
  for(const row of rows.filter(r=>selected.has(key(r)))){
    const rule=row.image_path?{image_path:row.image_path}:{pid:row.pid,created_at:row.created_at};
    if(!protections.some(r=>JSON.stringify(r)===JSON.stringify(rule)))protections.push(rule);
  }
  try{applyState(await api("/api/settings",{...settings,protections}));renderRows();}catch(exc){error(exc.message);}
});
function readRule(){return {mode:el("rule-mode").value,interval_s:Number(el("rule-interval").value),available_pct:Number(el("rule-threshold").value),sustain_s:Number(el("rule-sustain").value),cooldown_s:Number(el("rule-cooldown").value)};}
el("rule-form").addEventListener("submit",event=>{
  event.preventDefault();const targets=rows.filter(r=>selected.has(key(r)));
  confirmAction({kind:"rule-enable",targets:targets.map(key),rule:readRule()},`本次启用自动规则，仅处理当前 ${targets.length} 个身份快照并遵守当前保护规则。\n启用后本地服务可能持续读取内存并自动整理。关闭标签页不停止规则，请使用停用或停止服务。\n目标失效不自动替换，服务重启后关闭。`);
});
el("stop-rule").addEventListener("click",async()=>{try{applyState(await api("/api/rule",{enabled:false}));}catch(exc){error(exc.message);}});
function applyState(state){
  settings=state.settings;descending=settings.descending;
  el("rule-state").textContent=state.rule.armed?`规则已启用 · 固定 ${state.rule.target_count} 个身份`:`规则已停用${state.rule.error?" · "+state.rule.error:""}`;
  el("protections").replaceChildren();
  settings.protections.forEach((rule,index)=>{const li=document.createElement("li"),remove=document.createElement("button"),label=document.createElement("span");label.textContent=rule.image_path||`实例 PID ${rule.pid} / ${rule.created_at}`;remove.className="quiet";remove.textContent="移除";remove.setAttribute("aria-label",`移除保护 ${label.textContent}`);remove.addEventListener("click",async()=>{try{applyState(await api("/api/settings",{...settings,protections:settings.protections.filter((_,i)=>i!==index)}));renderRows();}catch(exc){error(exc.message);}});li.append(label,remove);el("protections").append(li);});
  if(!settings.protections.length){const li=document.createElement("li");li.textContent="暂无保护规则";el("protections").append(li);}
  if(state.rule.last_job && state.rule.last_job!==seenRuleJob){api(`/api/job?id=${encodeURIComponent(state.rule.last_job)}`).then(job=>{if(job.state==="finished"){seenRuleJob=job.id;if(job.kind==="batch")present(job);}else if(job.state==="error"){seenRuleJob=job.id;error(job.error);}}).catch(exc=>error(exc.message));}
  clearTimeout(ruleTimer);if(state.rule.armed&&!stopped)ruleTimer=setTimeout(async()=>{try{applyState(await api("/api/state"));controls();}catch(exc){error("服务连接失效；刷新页面前请确认原服务状态。 "+exc.message);}},1000);
  controls();
}
el("confirm-no").addEventListener("click",()=>{pendingAction=null;el("confirmation").hidden=true;});
el("confirm-yes").addEventListener("click",async()=>{const action=pendingAction;pendingAction=null;el("confirmation").hidden=true;if(!action)return;if(action.kind==="rule-enable"){try{applyState(await api("/api/rule",{enabled:true,confirmed:true,targets:action.targets,rule:action.rule}));}catch(exc){error(exc.message);}return;}if(action.kind==="shutdown"){try{await api("/api/shutdown",{confirmed:true});stopped=true;pauseDisplay();clearTimeout(ruleTimer);el("job-status").textContent="本地服务已请求停止";el("mode").textContent="服务已停止";controls();}catch(exc){error(exc.message);}return;}run(action);});
el("cancel").addEventListener("click",async()=>{if(currentJob)try{await api("/api/cancel",{id:currentJob});el("job-status").textContent="停止请求已登记，等待当前动作收尾";}catch(exc){error(exc.message);}});
el("replay").addEventListener("click",()=>run({kind:"replay",source_job:lastSaved}));
el("shutdown").addEventListener("click",()=>confirmAction({kind:"shutdown"},"停止本地服务并拒绝新任务。当前正在执行的动作会先收尾。\n关闭标签页本身不会停止服务。"));
el("view-scale").addEventListener("change",()=>{document.body.style.zoom=el("view-scale").value;});
controls();
api("/api/session").then(async session=>{
  token=session.token;supportedMethods=session.capabilities.mechanisms;readingSupported=session.reading==="available";
  el("mode").textContent=session.preview?"假数据 · Package 已连接":"真实系统 · Package 已连接";
  el("capability").textContent=`平台 ${session.operating_system} · 读取 ${session.reading} · 权限需在请求时核验`;
  el("boundary").textContent=session.preview?"隔离假数据模式：当前指标与进程全部为合成数据，不操作设备。":"真实系统模式：页面可见时持续更新内存；绿色按钮点击即整理一次工作集，全局范围不排除应用。";
  const state=await api("/api/state");applyState(state);
  if(settings.rule){const rule=settings.rule;el("rule-mode").value=rule.mode;el("rule-interval").value=rule.interval_s;el("rule-threshold").value=rule.available_pct;el("rule-sustain").value=rule.sustain_s;el("rule-cooldown").value=rule.cooldown_s;}
  el("sort").textContent=descending?"工作集 ↓":"工作集 ↑";controls();refreshDisplay();
}).catch(exc=>error(exc.message));
