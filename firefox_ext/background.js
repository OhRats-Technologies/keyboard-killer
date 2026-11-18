"use strict";

const WS_URL = "ws://127.0.0.1:8765";
let socket = null;
let reconnectTimer = null;

function connectWebSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  socket = new WebSocket(WS_URL);

  socket.onopen = () => {
    console.log("[Keyboard-Killer] Connected to local Python daemon at", WS_URL);
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  socket.onmessage = async (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (err) {
      return;
    }

    try {
      if (msg.type === "list_tabs") {
        const allTabs = await browser.tabs.query({});
        const mapped = allTabs.map((t) => ({
          id: t.id,
          windowId: t.windowId,
          index: t.index,
          active: Boolean(t.active),
          title: t.title || "",
          url: t.url || ""
        }));
        if (msg.req_id) {
          sendResponse({ req_id: msg.req_id, ok: true, data: { tabs: mapped } });
        }
        return;
      }

      if (msg.type === "switch_tab") {
        let targetTab = null;
        if (typeof msg.tab_id === "number") {
          targetTab = await browser.tabs.update(msg.tab_id, { active: true });
        } else if (msg.direction === "next" || msg.direction === "prev") {
          const winTabs = await browser.tabs.query({ lastFocusedWindow: true });
          if (winTabs.length > 0) {
            const activeIdx = Math.max(0, winTabs.findIndex((t) => t.active));
            const delta = msg.direction === "prev" ? -1 : 1;
            const nextIdx = (activeIdx + delta + winTabs.length) % winTabs.length;
            targetTab = await browser.tabs.update(winTabs[nextIdx].id, { active: true });
          }
        }
        if (targetTab && typeof targetTab.windowId === "number") {
          await browser.windows.update(targetTab.windowId, { focused: true }).catch(() => {});
        }
        if (msg.req_id) {
          sendResponse({
            req_id: msg.req_id,
            ok: Boolean(targetTab),
            data: targetTab ? { title: targetTab.title || "", id: targetTab.id } : null
          });
        }
        return;
      }

      if (msg.type === "new_tab") {
        const createProps = { active: true };
        if (msg.url) {
          createProps.url = msg.url;
        }
        const created = await browser.tabs.create(createProps);
        if (created && typeof created.windowId === "number") {
          await browser.windows.update(created.windowId, { focused: true }).catch(() => {});
        }
        if (msg.req_id) {
          sendResponse({
            req_id: msg.req_id,
            ok: Boolean(created),
            data: created ? { id: created.id, url: created.url || "" } : null
          });
        }
        return;
      }

      let tabs = await browser.tabs.query({ active: true, lastFocusedWindow: true });
      if (!tabs || tabs.length === 0) {
        tabs = await browser.tabs.query({ active: true, currentWindow: true });
      }
      if (!tabs || tabs.length === 0 || !tabs[0].id) {
        if (msg.req_id) {
          sendResponse({ req_id: msg.req_id, ok: false, error: "No active tab" });
        }
        return;
      }

      const activeTabId = tabs[0].id;
      let response;
      try {
        response = await browser.tabs.sendMessage(activeTabId, msg);
      } catch (sendErr) {
        const errText = String(sendErr || "");
        if (errText.includes("Receiving end does not exist") || errText.includes("Could not establish connection")) {
          await browser.tabs.executeScript(activeTabId, { file: "content_script.js", allFrames: false });
          response = await browser.tabs.sendMessage(activeTabId, msg);
        } else {
          throw sendErr;
        }
      }
      if (msg.req_id) {
        sendResponse({ req_id: msg.req_id, ok: true, data: response });
      }
    } catch (err) {
      if (msg.req_id) {
        sendResponse({ req_id: msg.req_id, ok: false, error: String(err) });
      }
    }
  };

  socket.onclose = () => {
    socket = null;
    if (!reconnectTimer) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket();
      }, 1200);
    }
  };

  socket.onerror = () => {
    if (socket) {
      socket.close();
    }
  };
}

function sendResponse(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

connectWebSocket();
