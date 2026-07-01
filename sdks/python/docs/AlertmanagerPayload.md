# AlertmanagerPayload


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**version** | **str** |  | [optional] 
**group_key** | **str** |  | [optional] 
**status** | **str** |  | 
**receiver** | **str** |  | [optional] 
**group_labels** | **Dict[str, str]** |  | [optional] 
**common_labels** | **Dict[str, str]** |  | [optional] 
**common_annotations** | **Dict[str, str]** |  | [optional] 
**external_url** | **str** |  | [optional] 
**alerts** | [**List[AlertmanagerAlert]**](AlertmanagerAlert.md) |  | [optional] 

## Example

```python
from rugmunch.models.alertmanager_payload import AlertmanagerPayload

# TODO update the JSON string below
json = "{}"
# create an instance of AlertmanagerPayload from a JSON string
alertmanager_payload_instance = AlertmanagerPayload.from_json(json)
# print the JSON string representation of the object
print(AlertmanagerPayload.to_json())

# convert the object into a dict
alertmanager_payload_dict = alertmanager_payload_instance.to_dict()
# create an instance of AlertmanagerPayload from a dict
alertmanager_payload_from_dict = AlertmanagerPayload.from_dict(alertmanager_payload_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


