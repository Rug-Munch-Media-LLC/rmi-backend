
# UsageResponse


## Properties

Name | Type
------------ | -------------
`agentId` | string
`dailyCalls` | number
`freeLimit` | number
`paidCalls` | number
`totalUsd` | number
`recentReceipts` | Array&lt;{ [key: string]: any; }&gt;

## Example

```typescript
import type { UsageResponse } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "agentId": null,
  "dailyCalls": null,
  "freeLimit": null,
  "paidCalls": null,
  "totalUsd": null,
  "recentReceipts": null,
} satisfies UsageResponse

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as UsageResponse
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


