from sqlalchemy.orm import Session

from app.agents.langgraph_runner import LangGraphWorkflowRunner


def resolve_workflow_engine(requested: str | None = None) -> str:
    return "langgraph"


def create_workflow_runner(db: Session, requested: str | None = None):
    return LangGraphWorkflowRunner(db), resolve_workflow_engine(requested)
