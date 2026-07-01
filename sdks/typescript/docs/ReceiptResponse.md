
# ReceiptResponse


## Properties

Name | Type
------------ | -------------
`txHash` | string
`tool` | string
`agentId` | string
`amountUsd` | number
`chain` | string
`paidAt` | string
`tier` | string

## Example

```typescript
import type { ReceiptResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "txHash": null,
  "tool": null,
  "agentId": null,
  "amountUsd": null,
  "chain": null,
  "paidAt": null,
  "tier": null,
} satisfies ReceiptResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ReceiptResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


