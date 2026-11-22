# agent 的 login shell:系統的 path_helper 跑完後,把 PATH 排回 agent_env 給的順序。
[ -n "$JOBSALVO_AGENT_PATH" ] && export PATH="$JOBSALVO_AGENT_PATH"
