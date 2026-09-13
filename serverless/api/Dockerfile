FROM public.ecr.aws/lambda/python:3.12

COPY serverless /var/task/serverless
COPY .codex/runtime/relations.sqlite3 /var/task/runtime/relations.sqlite3

CMD ["serverless.api.handler.lambda_handler"]
