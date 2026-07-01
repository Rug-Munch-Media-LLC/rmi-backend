# AlertmanagerAlert


## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**status** | **str** |  | 
**labels** | **Dict[str, str]** |  | [optional] 
**annotations** | **Dict[str, str]** |  | [optional] 
**starts_at** | **str** |  | [optional] 
**ends_at** | **str** |  | [optional] 
**generator_url** | **str** |  | [optional] 

## Example

```python
from rugmunch.models.alertmanager_alert import AlertmanagerAlert

# TODO update the JSON string below
json = "{}"
# create an instance of AlertmanagerAlert from a JSON string
alertmanager_alert_instance = AlertmanagerAlert.from_json(json)
# print the JSON string representation of the object
print(AlertmanagerAlert.to_json())

# convert the object into a dict
alertmanager_alert_dict = alertmanager_alert_instance.to_dict()
# create an instance of AlertmanagerAlert from a dict
alertmanager_alert_from_dict = AlertmanagerAlert.from_dict(alertmanager_alert_dict)
```
[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


