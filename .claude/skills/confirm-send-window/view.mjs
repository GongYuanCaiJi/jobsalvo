// /statechart 的 view:模型的狀態和步驟換成看板上的說法
const CARD = {
  parked: "停著等你",
  confirmed: "你已確認",
  sending: "正在送出",
  sent: "已送出",
  unsure: "送出結果不明",
  left: "離開這段(要重填)",
};
const RUN = { idle: "沒在送", launched: "送出那一輪派出去了", agent: "agent 在送" };

export const title = "確認送出到真的送出";

export const state = (v) => ({ 卡: CARD[v.ds], 送出那一輪: RUN[v.run.phase] });

const STEPS = {
  Tick: null,
  OtherJobToggles: null,
  YouConfirm: "你按確認送出",
  YouCancel: "你取消確認",
  ConfirmVoided: "確認作廢(換檔、頁不見、頁變了)",
  YouRestore: "你按復原放回確認",
  Refilled: "重填好了",
  SentElsewhere: "你標已在外部送出",
  YouSayNotSent: "你說沒送成",
  YouSayActuallySent: "你說其實送出了",
  YouReopenSent: "你把已送出的退回",
  TimerFires: "8 秒到",
  YouPressSend: "你按 ▶ 送出",
  StartSubmit: "送出那一輪開始送",
  LaunchFails: "送出前讀頁變了或沒開起來",
  AgentReturns: "agent 送完回報",
  AgentKilled: "送到一半被停",
  Settle: "程式收尾",
  ServerRestarts: "伺服器重開",
};

export const step = (action) => (action in STEPS ? STEPS[action] : action);
