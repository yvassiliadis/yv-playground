FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY server.py trivia.py trivia_output.json sweepstakes.html Golden_trophy_2.png world_cup_scopee.png ./

EXPOSE 8080

CMD ["uv", "run", "server.py"]
