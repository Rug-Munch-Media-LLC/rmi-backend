GET http://localhost:8000/api/v1/security/health
HTTP 200
[Asserts]
jsonpath "$.status" == "operational"
jsonpath "$.version" exists
