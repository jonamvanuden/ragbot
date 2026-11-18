cd /workspace/ragbot
source .venv/bin/activate

#STARTUP SERVER
uvicorn ragbot.server:app --host 0.0.0.0 --port 7777

#START UP ROUTING
python -u -m evals.router


#START UP JSON test
python -u -m ragbot.agent.json_test

#STARTUP SPEED
python -m playground.compare_models