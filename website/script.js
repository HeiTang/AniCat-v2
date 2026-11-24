document.querySelectorAll(".copy-button").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = button.closest("pre").querySelector("code").textContent.trim();
    const status = button.closest(".command").querySelector(".copy-status");
    try {
      await navigator.clipboard.writeText(text);
      status.textContent = "已複製，貼到終端機即可。";
    } catch {
      status.textContent = "無法存取剪貼簿，請選取上方指令手動複製。";
    }
  });
});
