
# Transaction

A wallet transaction summary.

## Properties

Name | Type
------------ | -------------
`hash` | string
`type` | string
`amountUsd` | number
`timestamp` | number
`blockTime` | Date
`direction` | string

## Example

```typescript
import type { Transaction } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "hash": null,
  "type": null,
  "amountUsd": null,
  "timestamp": null,
  "blockTime": null,
  "direction": null,
} satisfies Transaction

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as Transaction
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


