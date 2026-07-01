
# FeedbackRecord

Scanner → RAG feedback record.

## Properties

Name | Type
------------ | -------------
`tokenAddress` | string
`chain` | string
`safetyScore` | number
`riskFlags` | Array&lt;string&gt;
`action` | string
`source` | string

## Example

```typescript
import type { FeedbackRecord } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "tokenAddress": null,
  "chain": null,
  "safetyScore": null,
  "riskFlags": null,
  "action": null,
  "source": null,
} satisfies FeedbackRecord

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as FeedbackRecord
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


