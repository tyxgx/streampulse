FROM public.ecr.aws/lambda/python:3.14-arm64
COPY chatbot/requirements-lambda.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements-lambda.txt
COPY chatbot/agent.py chatbot/llm.py chatbot/tools.py chatbot/handler.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.handler"]
