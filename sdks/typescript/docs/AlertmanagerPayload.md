
# AlertmanagerPayload


## Properties

Name | Type
------------ | -------------
`version` | string
`groupKey` | string
`status` | string
`receiver` | string
`groupLabels` | { [key: string]: string; }
`commonLabels` | { [key: string]: string; }
`commonAnnotations` | { [key: string]: string; }
`externalURL` | string
`alerts` | [Array&lt;AlertmanagerAlert&gt;](AlertmanagerAlert.md)

## Example

```typescript
import type { AlertmanagerPayload } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "version": null,
  "groupKey": null,
  "status": null,
  "receiver": null,
  "groupLabels": null,
  "commonLabels": null,
  "commonAnnotations": null,
  "externalURL": null,
  "alerts": null,
} satisfies AlertmanagerPayload

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as AlertmanagerPayload
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


