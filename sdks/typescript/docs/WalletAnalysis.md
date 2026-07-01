
# WalletAnalysis

Full wallet analysis — risk + tokens + recent activity.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`truncatedAddress` | string
`riskScore` | number
`riskLevel` | [AppDomainTokenModelsRiskLevel](AppDomainTokenModelsRiskLevel.md)
`tokens` | [Array&lt;TokenHolding&gt;](TokenHolding.md)
`recentTransactions` | [Array&lt;Transaction&gt;](Transaction.md)
`labels` | Array&lt;string&gt;
`flags` | [Array&lt;ScanFlag&gt;](ScanFlag.md)
`totalValueUsd` | number
`analyzedAt` | Date

## Example

```typescript
import type { WalletAnalysis } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "truncatedAddress": null,
  "riskScore": null,
  "riskLevel": null,
  "tokens": null,
  "recentTransactions": null,
  "labels": null,
  "flags": null,
  "totalValueUsd": null,
  "analyzedAt": null,
} satisfies WalletAnalysis

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as WalletAnalysis
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


