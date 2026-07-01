
# TokenScanResult

Full token scan result.

## Properties

Name | Type
------------ | -------------
`address` | string
`chain` | string
`safetyScore` | number
`riskScore` | number
`riskLevel` | [AppDomainTokenModelsRiskLevel](AppDomainTokenModelsRiskLevel.md)
`riskFlags` | Array&lt;string&gt;
`warnings` | Array&lt;string&gt;
`modulesRun` | Array&lt;string&gt;
`metadata` | { [key: string]: any; }
`scannedAt` | Date

## Example

```typescript
import type { TokenScanResult } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "address": null,
  "chain": null,
  "safetyScore": null,
  "riskScore": null,
  "riskLevel": null,
  "riskFlags": null,
  "warnings": null,
  "modulesRun": null,
  "metadata": null,
  "scannedAt": null,
} satisfies TokenScanResult

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as TokenScanResult
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


