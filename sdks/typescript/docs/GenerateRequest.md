
# GenerateRequest


## Properties

Name | Type
------------ | -------------
`subjectType` | string
`subjectId` | string
`model` | string
`save` | boolean

## Example

```typescript
import type { GenerateRequest } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "subjectType": null,
  "subjectId": null,
  "model": null,
  "save": null,
} satisfies GenerateRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as GenerateRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


