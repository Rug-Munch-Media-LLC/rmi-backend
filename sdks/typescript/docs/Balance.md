
# Balance

Wallet balance + token holdings.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`nativeBalance` | number
`nativeSymbol` | string
`totalValueUsd` | number
`tokens` | [Array&lt;TokenHolding&gt;](TokenHolding.md)
`fetchedAt` | Date

## Example

```typescript
import type { Balance } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "nativeBalance": null,
  "nativeSymbol": null,
  "totalValueUsd": null,
  "tokens": null,
  "fetchedAt": null,
} satisfies Balance

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as Balance
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


