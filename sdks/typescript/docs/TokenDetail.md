
# TokenDetail

Full token details — metadata + supply + verification.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`name` | string
`symbol` | string
`decimals` | number
`totalSupply` | number
`circulatingSupply` | number
`holders` | number
`verified` | boolean
`metadata` | { [key: string]: any; }
`fetchedAt` | Date

## Example

```typescript
import type { TokenDetail } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "name": null,
  "symbol": null,
  "decimals": null,
  "totalSupply": null,
  "circulatingSupply": null,
  "holders": null,
  "verified": null,
  "metadata": null,
  "fetchedAt": null,
} satisfies TokenDetail

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as TokenDetail
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


