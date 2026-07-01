
# AlertmanagerAlert


## Properties

Name | Type
------------ | -------------
`status` | string
`labels` | { [key: string]: string; }
`annotations` | { [key: string]: string; }
`startsAt` | string
`endsAt` | string
`generatorURL` | string

## Example

```typescript
import type { AlertmanagerAlert } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "status": null,
  "labels": null,
  "annotations": null,
  "startsAt": null,
  "endsAt": null,
  "generatorURL": null,
} satisfies AlertmanagerAlert

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as AlertmanagerAlert
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


