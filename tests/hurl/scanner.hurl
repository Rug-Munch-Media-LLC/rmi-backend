POST http://localhost:8000/scanner/scan
[Options]
variable: token_address = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
{
  "chain": "ethereum",
  "address": "{{token_address}}"
}
HTTP 501
[Asserts]
jsonpath "$.detail" contains "not yet wired"
