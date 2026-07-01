
# ScanFlag

A risk flag raised by a scan.

## Properties

Name | Type
------------ | -------------
`code` | string
`severity` | [AppDomainTokenModelsRiskLevel](AppDomainTokenModelsRiskLevel.md)
`message` | string
`evidence` | { [key: string]: any; }

## Example

```typescript
import type { ScanFlag } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "code": null,
  "severity": null,
  "message": null,
  "evidence": null,
} satisfies ScanFlag

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ScanFlag
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


