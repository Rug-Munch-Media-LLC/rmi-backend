
# TokenHolding

A single token holding within a wallet\'s portfolio.

## Properties

Name | Type
------------ | -------------
`symbol` | string
`address` | string
`amount` | number
`priceUsd` | number
`valueUsd` | number

## Example

```typescript
import type { TokenHolding } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "symbol": null,
  "address": null,
  "amount": null,
  "priceUsd": null,
  "valueUsd": null,
} satisfies TokenHolding

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as TokenHolding
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


