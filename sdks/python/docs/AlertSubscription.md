# AlertSubscription

A token alert subscription record (what we store in Redis).

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**id** | **str** | Unique subscription id, e.g. alert:1700000000 | 
**token_address** | **str** |  | 
**alert_types** | [**List[AlertType]**](AlertType.md) |  | [optional] 
**webhook_url** | **str** |  | [optional] 
**created_at** | **datetime** |  | [optional] 
**active** | **bool** |  | [optional] [default to True]
**owner_id** | **str** | User id of subscription owner, None &#x3D; anonymous | [optional] 

## Example

```python
from rugmunch.models.alert_subscription import AlertSubscription

# TODO update the JSON string below
json = "{}"
# create an instance of AlertSubscription from a JSON string
alert_subscription_instance = AlertSubscription.from_json(json)
# print the JSON string representation of the object
print(AlertSubscription.to_json())

# convert the object into a dict
alert_subscription_dict = alert_subscription_instance.to_dict()
# create an instance of AlertSubscription from a dict
alert_subscription_from_dict = AlertSubscription.from_dict(alert_subscription_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


