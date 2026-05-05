"""多智能体协作。

每个智能体只是一组 LLM prompt + 业务逻辑封装，由 orchestrator 调度。
真实部署可以替换成独立 Agent 服务（LangGraph / CrewAI / Autogen 等）。
"""
