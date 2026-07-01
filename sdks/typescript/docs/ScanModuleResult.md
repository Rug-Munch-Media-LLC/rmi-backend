
# ScanModuleResult

A single scanner module\'s outcome.

## Properties

Name | Type
------------ | -------------
`module` | string
`status` | string
`error` | string
`data` | { [key: string]: any; }

## Example

```typescript
import type { ScanModuleResult } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "module": null,
  "status": null,
  "error": null,
  "data": null,
} satisfies ScanModuleResult

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as ScanModuleResult
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


