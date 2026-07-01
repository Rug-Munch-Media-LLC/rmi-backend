
# TokenRisk

Token risk assessment.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`riskScore` | number
`riskLevel` | [AppDomainTokenModelsRiskLevel](AppDomainTokenModelsRiskLevel.md)
`flags` | Array&lt;{ [key: string]: any; }&gt;
`honeypot` | boolean
`canSell` | boolean
`canBuy` | boolean
`takeBack` | boolean
`ownerChangeBalance` | boolean
`transferPausable` | boolean
`analyzedAt` | Date

## Example

```typescript
import type { TokenRisk } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "riskScore": null,
  "riskLevel": null,
  "flags": null,
  "honeypot": null,
  "canSell": null,
  "canBuy": null,
  "takeBack": null,
  "ownerChangeBalance": null,
  "transferPausable": null,
  "analyzedAt": null,
} satisfies TokenRisk

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as TokenRisk
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


