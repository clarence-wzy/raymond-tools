"use strict";

const tools = ["video_downloader"];
const titles = { video_downloader: "视频下载" };

function selectTool() {
  const selected = tools.includes(location.hash.slice(1)) ? location.hash.slice(1) : tools[0];
  for (const tool of tools) {
    const frame = document.getElementById(tool);
    const link = document.getElementById(`nav-${tool}`);
    frame.hidden = tool !== selected;
    if (tool === selected) {
      if (!frame.getAttribute("src")) frame.src = frame.dataset.src;
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  }
  document.title = `${titles[selected]} · Video Tools`;
}

window.addEventListener("hashchange", selectTool);
selectTool();
