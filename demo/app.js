"use strict";

const percent = document.getElementById("percent");
const available = document.getElementById("available");
const fill = document.getElementById("fill");
const usage = document.getElementById("usage");
const clean = document.getElementById("clean");
const revisit = document.getElementById("revisit");
const reset = document.getElementById("reset");
const status = document.getElementById("status");
const message = document.getElementById("message");
let current = 76;
let phase = "ready";
let frame = null;

function paint(value) {
  current = value;
  percent.textContent = Math.round(value);
  available.textContent = (16 * (1 - value / 100)).toFixed(2);
  fill.style.width = value + "%";
  usage.setAttribute("aria-valuenow", String(Math.round(value)));
}

function animate(target, done) {
  const start = performance.now();
  const from = current;
  const duration = matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 1400;
  function tick(now) {
    const progress = duration === 0 ? 1 : Math.min(1, Math.max(0, (now - start) / duration));
    paint(from + (target - from) * progress * progress * (3 - 2 * progress));
    if (progress < 1) {
      frame = requestAnimationFrame(tick);
    } else {
      frame = null;
      paint(target);
      done();
    }
  }
  frame = requestAnimationFrame(tick);
}

clean.addEventListener("click", () => {
  if (phase !== "ready") return;
  phase = "cleaning";
  clean.disabled = true;
  status.textContent = "正在模拟工作集整理…";
  message.textContent = "动画用于演示占用变化，不是真实清理进度。";
  animate(48, () => {
    phase = "cleaned";
    revisit.disabled = false;
    status.textContent = "模拟整理完成";
    message.textContent = "可用内存 3.84 → 8.32 GiB，提交量保持 18.20 GiB。预设差额不代表真实释放量或提速。";
  });
});

revisit.addEventListener("click", () => {
  if (phase !== "cleaned") return;
  phase = "revisiting";
  revisit.disabled = true;
  status.textContent = "正在模拟程序再次访问页面…";
  message.textContent = "数据重新进入工作集，占用可能回升；需要读磁盘时可能产生等待。";
  animate(69, () => {
    phase = "revisited";
    status.textContent = "模拟再次使用完成";
    message.textContent = "占用回升到 69%。程序仍在运行，整理改变驻留状态，没有替应用删除数据。可重置后再次体验。";
  });
});

reset.addEventListener("click", () => {
  if (frame !== null) cancelAnimationFrame(frame);
  frame = null;
  phase = "ready";
  paint(76);
  clean.disabled = false;
  revisit.disabled = true;
  status.textContent = "准备就绪";
  message.textContent = "点击按钮，查看一次预设的整理过程。";
});
