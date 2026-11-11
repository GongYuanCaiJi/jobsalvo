// /statechart 的 view:模型的狀態和步驟換成看板上的說法
const APP = { before: "還沒進待你決定", ready: "待你決定", ship: "可以投了" };
const DS = {
  todo: "排隊等填", running: "正在填", parked: "填好停著", stuck: "填了卡住",
  gone: "頁不見了", stale: "上傳的是舊檔", nopage: "沒填成", left: "離開這段",
};
const JOB = { none: "沒在填", launched: "填表那一輪派出去了", started: "agent 在填" };

export const title = "自動流程:推進可以投了、自動填表";

export const state = (v) => ({
  卡: v.app === "ship" ? `${APP.ship}・${DS[v.dsv]}` : APP[v.app],
  看板上的驗收: v.status.start === 0 ? "還沒有" : `第 ${v.status.start} 刻開始的那一輪:${v.status.cardIn ? (v.status.bad ? "這張不過" : "這張過") : "沒驗到這張"}`,
  建置: v.build.running ? "在跑" : `最近跑完的是第 ${v.build.built} 刻開始的`,
  填表: JOB[v.job],
});

const STEPS = {
  Enter: "卡進待你決定",
  YouSave: "存檔,背景開始建置",
  ReconcileReads: "驗收讀看板",
  ReconcileWrites: "驗收寫進結果",
  BuildDone: "建置跑完",
  BuildFails: "建置沒跑完",
  JobFillStart: "agent 開始填",
  JobQuitsEarly: "那一輪還沒動卡就結束",
  JobEnds: "agent 填完回報",
  JobKilled: "填到一半被停",
  Settle: "程式收尾(那一輪不在跑)",
  PageLost: "頁不見了(ego 工作區或分頁不在了)",
  PilotReads: "自動流程讀看板檔",
  PilotPlans: "自動流程讀建置狀態、決定要按什麼",
  PilotLaunches: "自動流程派工",
  PilotWrites: "自動流程寫回看板(記試過、推進)",
  ServerRestarts: "伺服器重開",
};

export const step = (action) => (action in STEPS ? STEPS[action] : action);
