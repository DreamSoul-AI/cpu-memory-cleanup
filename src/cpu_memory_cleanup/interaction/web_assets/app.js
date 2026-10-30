"use strict";
const el = (id) => document.getElementById(id);
let token = "", busy = false, currentJob = null, lastSaved = null, pendingAction = null;
let rows = [], selected = new Set(), descending = true, stopped = false;
const key = (row) => `${row.pid}:${row.created_at}`;
const gib = (value) => value == null ? "未知" : `${(value / 1024 ** 3).toFixed(2)} GiB`;
const labels = {success:"完成", partial:"结果不完整", failed:"失败", cancelled:"已取消", skipped:"已跳过", not_started:"未执行"};
function error(message) { el("error").textContent = message; el("error").hidden = !message; }
function controls() {
  for (const id of ["read-status","read-targets","global-clean","shutdown"]) el(id).disabled = busy || stopped || !token;
  el("selected-clean").disabled = busy || stopped || !selected.size || !token;
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
  for (const row of visible) {
    const tr=document.createElement("tr"), td=document.createElement("td"), checkbox=document.createElement("input");
    checkbox.type="checkbox"; checkbox.checked=selected.has(key(row)); checkbox.disabled=busy;
    checkbox.setAttribute("aria-label",`选择 ${row.name} PID ${row.pid}`);
    checkbox.addEventListener("change",()=>{checkbox.checked?selected.add(key(row)):selected.delete(key(row)); controls(); count();});
    td.append(checkbox);tr.append(td);
    const name=document.createElement("td"), title=document.createElement("strong"), path=document.createElement("small");
    title.textContent=row.name;path.textContent=row.image_path||"路径未知";name.append(title,path);tr.append(name);
    for(const text of [String(row.pid),gib(row.working_set_bytes)]){const cell=document.createElement("td");cell.textContent=text;tr.append(cell);}
    el("targets").append(tr);
  }
  if(!visible.length){const tr=document.createElement("tr"),td=document.createElement("td");td.colSpan=4;td.className="empty";td.textContent=rows.length?"没有匹配的进程。":"按需读取列表，然后选择进程。";tr.append(td);el("targets").append(tr);}
  count();
}
function count(){el("selection-count").textContent=`${rows.length} 个目标 · 已选 ${selected.size} 个`;}
function memory(report){
  const sample=report.samples?.at(-1);if(!sample)return;
  const physical=sample.metrics.physical, commit=sample.metrics.commit;
  if(!physical)return;
  const {total_bytes:total,available_bytes:available}=physical;
  const valid=total>0 && available!=null && available>=0 && available<=total;
  const pct=valid?(total-available)/total*100:null;
  el("percent").textContent=pct==null?"未知":`${Math.round(pct)}%`;
  el("fill").style.width=`${pct||0}%`;if(pct!=null)el("meter").setAttribute("aria-valuenow",String(Math.round(pct)));else el("meter").removeAttribute("aria-valuenow");
  el("capacity").textContent=`${gib(available)} / ${gib(total)}`;
  el("commit").textContent=`${gib(commit?.used_bytes)} / ${gib(commit?.limit_bytes)}`;
}
function present(job){
  const report=job.report;memory(report);
  if(job.kind==="targets"){
    rows=report.samples[0]?.processes||[];const present=new Set(rows.map(key));selected=new Set([...selected].filter(k=>present.has(k)));renderRows();
    if(report.completion!=="success") error("列表可能不完整，请查看读取错误；可读项仍可选择。");
    return;
  }
  if(job.kind==="status") return;
  el("result-items").replaceChildren();
  const action=report.action||{};
  const actionText={success:"调用成功",failure:"调用失败",not_started:"未派发",unknown:"调用结果未知"}[action.call_outcome]||"逐项结果见下方";
  el("result-summary").textContent=`${labels[report.completion]||report.completion} · ${job.kind==="batch" || report.mode==="batch-review"?"逐项记录，不累加为系统释放量":actionText}。容量变化不代表提速。`;
  const items=report.metadata?.items||[];
  for(const item of items){const div=document.createElement("div");div.className="result-line";const a=item.analysis||{};div.textContent=`${item.target.name||"目标"} · PID ${item.target.pid} · ${labels[item.status]||item.status} · 工作集 ${gib(a.working_set_before_bytes)} → ${gib(a.working_set_after_bytes)} ${item.reason||""}`;el("result-items").append(div);}
  if(!items.length && report.samples?.length>=2){const div=document.createElement("div");div.className="result-line";div.textContent=`系统可用量：${gib(report.samples[0].metrics.physical?.available_bytes)} → ${gib(report.samples.at(-1).metrics.physical?.available_bytes)}`;el("result-items").append(div);}
  el("report").textContent=JSON.stringify(report,null,2);
  if(report.output_dir && job.kind!=="replay")lastSaved=job.id;
}
async function run(action){
  if(busy||stopped)return;error("");busy=true;controls();renderRows();
  el("job-status").textContent="正在提交本次请求…";
  try{
    const accepted=await api("/api/action",action);currentJob=accepted.id;
    el("job-status").textContent="处理中 · 等待 Package 收尾";
    for(;;){const job=await api(`/api/job?id=${encodeURIComponent(currentJob)}`);if(job.state!=="running"){if(job.state==="error")throw new Error(job.error);present(job);el("job-status").textContent=`本次${labels[job.report.completion]||job.report.completion}`;break;}await new Promise(resolve=>setTimeout(resolve,120));}
  }catch(exc){error(exc.message);el("job-status").textContent="本次未完成，请核对结果；不自动重试整理。";}
  finally{busy=false;currentJob=null;controls();renderRows();}
}
function confirmAction(action,text){pendingAction=action;el("confirmation-text").textContent=text;el("confirmation").hidden=false;el("confirm-yes").focus();}
el("read-status").addEventListener("click",()=>run({kind:"status"}));
el("read-targets").addEventListener("click",()=>run({kind:"targets"}));
el("search").addEventListener("input",renderRows);
el("sort").addEventListener("click",()=>{descending=!descending;el("sort").textContent=descending?"工作集 ↓":"工作集 ↑";renderRows();});
el("global-clean").addEventListener("click",()=>confirmAction({kind:"cleanup",confirmed:true},"本次执行一次全局工作集整理。\n不支持应用排除，可能增加后续缺页和 I/O。"));
el("selected-clean").addEventListener("click",()=>{const targets=rows.filter(r=>selected.has(key(r)));confirmAction({kind:"batch",targets:targets.map(key),confirmed:true},`仅整理以下 ${targets.length} 个进程：\n${targets.map(r=>`${r.name} · PID ${r.pid}`).join("\n")}\n不处理列表之外的其他进程。`);});
el("confirm-no").addEventListener("click",()=>{pendingAction=null;el("confirmation").hidden=true;});
el("confirm-yes").addEventListener("click",async()=>{const action=pendingAction;pendingAction=null;el("confirmation").hidden=true;if(!action)return;if(action.kind==="shutdown"){try{await api("/api/shutdown",{confirmed:true});stopped=true;el("job-status").textContent="本地服务已请求停止";el("mode").textContent="服务已停止";controls();}catch(exc){error(exc.message);}return;}run(action);});
el("cancel").addEventListener("click",async()=>{if(currentJob)try{await api("/api/cancel",{id:currentJob});el("job-status").textContent="停止请求已登记，等待当前动作收尾";}catch(exc){error(exc.message);}});
el("replay").addEventListener("click",()=>run({kind:"replay",source_job:lastSaved}));
el("shutdown").addEventListener("click",()=>confirmAction({kind:"shutdown"},"停止本地服务并拒绝新任务。当前正在执行的动作会先收尾。\n关闭标签页本身不会停止服务。"));
controls();
api("/api/session").then(session=>{token=session.token;el("mode").textContent=session.preview?"假数据 · Package 已连接":"本地 Package 接入";if(session.preview)el("boundary").textContent="隔离假数据模式：按钮调用真实 Package 流程，指标与进程全部为合成数据，不操作你的设备。";controls();}).catch(exc=>error(exc.message));
