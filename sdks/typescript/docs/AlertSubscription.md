
# AlertSubscription

A token alert subscription record (what we store in Redis).

## Properties

Name | Type
------------ | -------------
`id` | string
`tokenAddress` | string
`alertTypes` | [Array&lt;AlertType&gt;](AlertType.md)
`webhookUrl` | string
`createdAt` | Date
`active` | boolean
`ownerId` | string

## Example

```typescript
import type { AlertSubscription } from '@rugmunch/sdk'

// TODO: Update the object below with actual values
const example = {
  "id": null,
  "tokenAddress": null,
  "alertTypes": null,
  "webhookUrl": null,
  "createdAt": null,
  "active": null,
  "ownerId": null,
} satisfies AlertSubscription

console.log(example)

// Convert the instance to a JSON string
const exampleJSON: string = JSON.stringify(example)
console.log(exampleJSON)

// Parse the JSON string back to an object
const exampleParsed = JSON.parse(exampleJSON) as AlertSubscription
console.log(exampleParsed)
```

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


