---
name: Daily Attendance & Health Loop
description: ''
agent_key: null
skills: []
default_config:
  execution_mode: loop
  frequency_sec: 86400
  tick_timeout_sec: 900
default_trading_context: ''
created_by: 358518143
created_at: '2026-09-15T20:05:54.101766+00:00'
---

Daily loop for the HR Department at zbot. Once per day: run the attendance + health check from your AGENT.md. Verify all 5 employees are running (manage_trading_agent list_agents), journal freshness for each (trading_agent_journal_read), and position protection on desks (stop + trailing). Write one concise journal entry (trading_agent_journal_write) with each employee PRESENT/ABSENT + HEALTHY/UNHEALTHY. Escalate via send_notification to the owner ONLY if someone is absent or unhealthy, or if any employee missed 2+ consecutive cycles. Keep your own rolling health log in your journal. You never trade — you are read-only oversight, the independent backup to CEO zbot.
