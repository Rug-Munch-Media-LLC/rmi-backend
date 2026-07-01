
# ScanResult

Threat scan result.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`riskScore` | number
`riskLevel` | [AppDomainTokenModelsRiskLevel](AppDomainTokenModelsRiskLevel.md)
`flags` | [Array&lt;ScanFlag&gt;](ScanFlag.md)
`modulesRun` | Array&lt;string&gt;
`scannedAt` | Date

## Example

```typescript
import type { ScanResult } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "riskScore": null,
  "riskLevel": null,
  "flags": null,
  "modulesRun": null,
  "scannedAt": null,
} satisfies ScanResult

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ScanResult
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


