(function () {
  var status = document.getElementById("status");
  var title = document.getElementById("title");
  var ping = document.getElementById("ping");
  status.textContent = "served via tkwry:// (relative CSS/JS OK)";
  title.dataset.ready = "1";
  ping.addEventListener("click", function () {
    if (window.ipc && window.ipc.postMessage) {
      window.ipc.postMessage("ping");
      status.textContent = "sent IPC ping";
    } else {
      status.textContent = "window.ipc unavailable";
    }
  });
})();
