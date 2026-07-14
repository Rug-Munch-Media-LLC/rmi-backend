GET http://localhost:4000/health
HTTP 401
[Asserts]
jsonpath "$.error.type" == "auth_error"
jsonpath "$.error.message" contains "No api key"
