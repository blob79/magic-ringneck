FROM python:3.12-bookworm

ENV HOME /home/developer
ENV PYTHONUNBUFFERED="1"
WORKDIR /home/developer
COPY . .
RUN apt update && \
    apt install -y uuid-runtime screen neovim && \
    pip install build && \
    pip install -e .[test,dev] && \
    python -m build && \
    pip install dist/ringneck-0.0.1-py3-none-any.whl

CMD [ "/usr/bin/bash" ]
