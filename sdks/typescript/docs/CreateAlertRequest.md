
# CreateAlertRequest

What the API accepts to create a subscription.

## Properties

Name | Type
------------ | -------------
`tokenAddress` | string
`alertTypes` | [Array&lt;AlertType&gt;](AlertType.md)
`webhookUrl` | string

## Example

```typescript
import type { CreateAlertRequest } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "tokenAddress": null,
  "alertTypes": null,
  "webhookUrl": null,
} satisfies CreateAlertRequest

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as CreateAlertRequest
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


